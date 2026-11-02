import type { MessageDescriptor } from '@/i18n/messages'
import { apiFetch, readJson, createAuthenticatedWebSocket, isUnauthorizedClose, notifyUnauthorized } from '@/services/http'

const API_BASE = '/ama/api'

export interface FolderInfo {
    name: string
    path: string
    pdf_count: number
    modified: string
}

export interface BatchDirectoryNode {
    name: string
    path: string
    modified: string
    children: BatchDirectoryNode[]
    pdfs: PdfInfo[]
    pdf_count: number
}

export interface PdfInfo {
    name: string
    path: string
    size: number
    is_attachment?: boolean
    attachment_count?: number
}

export interface BatchTask {
    id: string
    pdf_path: string
    status: 'pending' | 'running' | 'done' | 'error' | 'cancelled' | 'stopped'
    retry_count: number
    error: string | null
    start_time: string | null
    end_time: string | null
    current_step: string | null
    step_i18n?: MessageDescriptor
    error_i18n?: MessageDescriptor
    diagnostic?: string
    result_task_id: string | null
    report_status?: 'none' | 'partial' | 'complete'
}

export interface BatchStatus {
    job_id: string | null
    session_id: string | null
    status: 'queued' | 'running' | 'stopping' | 'stopped' | 'completed' | 'failed' | 'interrupted'
    status_reason: 'normal' | 'stop_requested' | 'lease_expired' | 'reporter_timeout' | 'mineru_timeout' | 'llm_timeout' | 'worker_error'
    last_error: string | null
    error_i18n?: MessageDescriptor
    diagnostic?: string
    is_running: boolean
    total: number
    concurrency: number
    experiment_rounds: number
    current_round: number
    pending: number
    running: number
    done: number
    error: number
    cancelled: number
    stopped: number
    stop_requested: boolean
    tasks: BatchTask[]
}

export interface ResumeLatestResponse {
    available: boolean
    session_id: string | null
    last_updated: string | null
    resume_round: number | null
    remaining_tasks: number
    status: BatchStatus | null
}

export interface SessionInfo {
    session_id: string
    created_at: string
    last_updated: string
    total_tasks: number
    done: number
    error: number
    running: number
    pending: number
    is_completed: boolean
}

export interface FolderTreeResponse {
    tree: BatchDirectoryNode
    folders: FolderInfo[]
    input_dir: string
}

export const batchApi = {
    async listFolders(): Promise<FolderTreeResponse> {
        const res = await apiFetch(`${API_BASE}/batch/folders`)

        return readJson<FolderTreeResponse>(res)
    },

    async listPdfs(folderName: string): Promise<PdfInfo[]> {
        const res = await apiFetch(`${API_BASE}/batch/folders/${encodeURIComponent(folderName)}/pdfs`)

        const data = await readJson<{ pdfs: PdfInfo[] }>(res)
        return data.pdfs
    },

    async start(folder: string, concurrency: number): Promise<BatchStatus> {
        const res = await apiFetch(`${API_BASE}/batch/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder, concurrency }),
        })

        return readJson<BatchStatus>(res)
    },

    async stop(jobId?: string | null): Promise<BatchStatus> {
        const targetUrl = jobId ? `${API_BASE}/batch/jobs/${encodeURIComponent(jobId)}/stop` : `${API_BASE}/batch/stop`
        const res = await apiFetch(targetUrl, {
            method: 'POST',
        })

        const data = await readJson<BatchStatus | { status: BatchStatus }>(res)
        return 'tasks' in data ? data : data.status
    },

    async getStatus(): Promise<BatchStatus> {
        const res = await apiFetch(`${API_BASE}/batch/status`)

        return readJson<BatchStatus>(res)
    },

    async getJobStatus(jobId: string): Promise<BatchStatus> {
        const res = await apiFetch(`${API_BASE}/batch/jobs/${encodeURIComponent(jobId)}`)

        return readJson<BatchStatus>(res)
    },

    async getLatestResumable(): Promise<ResumeLatestResponse> {
        const res = await apiFetch(`${API_BASE}/batch/jobs/latest-resumable`)

        return readJson<ResumeLatestResponse>(res)
    },

    async resumeLatest(concurrency?: number): Promise<BatchStatus> {
        const res = await apiFetch(`${API_BASE}/batch/resume/latest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(concurrency ? { concurrency } : {}),
        })

        return readJson<BatchStatus>(res)
    },

    async startSelected(pdfPaths: string[], concurrency: number, experimentRounds: number = 1): Promise<BatchStatus> {
        const res = await apiFetch(`${API_BASE}/batch/start-selected`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pdf_paths: pdfPaths,
                concurrency,
                experiment_rounds: experimentRounds
            }),
        })

        return readJson<BatchStatus>(res)
    },

    async listSessions(): Promise<SessionInfo[]> {
        const res = await apiFetch(`${API_BASE}/batch/sessions`)

        const data = await readJson<{ sessions: SessionInfo[] }>(res)
        return data.sessions
    },

    async restoreSession(sessionId: string): Promise<{ message: string; session_id: string; status: BatchStatus }> {
        const res = await apiFetch(`${API_BASE}/batch/restore/${encodeURIComponent(sessionId)}`, {
            method: 'POST',
        })

        return readJson<{ message: string; session_id: string; status: BatchStatus }>(res)
    },

    async deleteSession(sessionId: string): Promise<{ message: string }> {
        const res = await apiFetch(`${API_BASE}/batch/sessions/${encodeURIComponent(sessionId)}`, {
            method: 'DELETE',
        })

        return readJson<{ message: string }>(res)
    },
}

export interface BatchWebSocketOptions {
    sessionId: string
    onTaskUpdate: (task: BatchTask) => void
    onComplete: (status: BatchStatus) => void
    onError?: (error: string) => void
    onConnectionChange?: (isConnected: boolean, reconnectAttempts: number) => void
}

export interface BatchWebSocketHandle {
    ws: WebSocket
    reconnect: () => void
    disconnect: () => void
}

export function createBatchWebSocket(options: BatchWebSocketOptions): BatchWebSocketHandle {
    const { sessionId, onTaskUpdate, onComplete, onError, onConnectionChange } = options

    const state = {
        reconnectAttempts: 0,
        maxReconnectAttempts: 5,
        reconnectTimeout: null as ReturnType<typeof setTimeout> | null,
        heartbeatInterval: null as ReturnType<typeof setInterval> | null,
        manuallyClosed: false,
        ws: null as WebSocket | null
    }

    const startHeartbeat = () => {
        if (state.heartbeatInterval) {
            clearInterval(state.heartbeatInterval)
        }
        state.heartbeatInterval = setInterval(() => {
            if (state.ws?.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify({ type: 'ping' }))
            }
        }, 30000)
    }

    const stopHeartbeat = () => {
        if (state.heartbeatInterval) {
            clearInterval(state.heartbeatInterval)
            state.heartbeatInterval = null
        }
    }

    const stopReconnect = () => {
        if (state.reconnectTimeout) {
            clearTimeout(state.reconnectTimeout)
            state.reconnectTimeout = null
        }
        state.reconnectAttempts = 0
    }

    const scheduleReconnect = () => {
        if (state.manuallyClosed) {
            return
        }
        if (state.reconnectTimeout) {
            clearTimeout(state.reconnectTimeout)
        }
        const delay = Math.min(1000 * Math.pow(2, state.reconnectAttempts), 30000)
        console.log(`批量任务 WebSocket 将在 ${delay}ms 后尝试重连 (尝试 ${state.reconnectAttempts}/${state.maxReconnectAttempts})`)
        onConnectionChange?.(false, state.reconnectAttempts + 1)

        state.reconnectTimeout = setTimeout(() => {
            if (state.manuallyClosed) {
                return
            }
            if (state.reconnectAttempts < state.maxReconnectAttempts) {
                state.reconnectAttempts++
                const newWs = createAuthenticatedWebSocket(`/batch/ws/${sessionId}`)
                setupWebSocketHandlers(newWs)
                state.ws = newWs
            } else {
                console.log('批量任务 WebSocket 已达到最大重连次数，停止重连')
                onConnectionChange?.(false, 0)
            }
        }, delay)
    }

    const setupWebSocketHandlers = (ws: WebSocket) => {
        ws.onopen = () => {
            console.log('批量任务 WebSocket 已连接')
            state.manuallyClosed = false
            state.reconnectAttempts = 0
            stopReconnect()
            onConnectionChange?.(true, 0)
            startHeartbeat()
        }

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data)
                if (data.type === 'task_update') {
                    onTaskUpdate(data.task)
                } else if (data.type === 'batch_complete') {
                    onComplete(data.status)
                } else if (data.type === 'status') {
                    onComplete(data.status)
                } else if (data.type === 'pong') {
                    console.debug('收到批量任务心跳响应')
                }
            } catch (e) {
                console.error('批量任务 WebSocket 消息解析失败:', e)
            }
        }

        ws.onerror = () => {
            console.error('批量任务 WebSocket 连接错误')
            onError?.('WebSocket 连接错误')
        }

        ws.onclose = (event) => {
            console.log('批量任务 WebSocket 已断开')
            stopHeartbeat()
            onConnectionChange?.(false, state.reconnectAttempts)

            if (state.manuallyClosed) {
                return
            }

            if (isUnauthorizedClose(event)) {
                notifyUnauthorized()
                return
            }

            if (state.reconnectAttempts < state.maxReconnectAttempts) {
                scheduleReconnect()
            }
        }
    }

    const reconnect = () => {
        console.log('手动触发批量任务 WebSocket 重连')
        state.manuallyClosed = false
        if (state.ws) {
            state.ws.close()
        }
        state.reconnectAttempts = 0
        const newWs = createAuthenticatedWebSocket(`/batch/ws/${sessionId}`)
        setupWebSocketHandlers(newWs)
        state.ws = newWs
    }

    const disconnect = () => {
        state.manuallyClosed = true
        stopReconnect()
        stopHeartbeat()
        if (state.ws) {
            state.ws.close()
            state.ws = null
        }
    }

    const ws = createAuthenticatedWebSocket(`/batch/ws/${sessionId}`)
    setupWebSocketHandlers(ws)
    state.ws = ws

    return {
        ws,
        reconnect,
        disconnect
    }
}
