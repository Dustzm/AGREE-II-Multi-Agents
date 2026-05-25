import { apiFetch, createAuthenticatedWebSocket, isUnauthorizedClose, notifyUnauthorized } from '@/services/http'

const API_BASE = '/ama/api'

async function parseJsonSafely<T>(res: Response): Promise<T | null> {
    const text = await res.text()
    if (!text) {
        return null
    }

    try {
        return JSON.parse(text) as T
    } catch (error) {
        console.error('批量接口返回了无法解析的 JSON:', {
            url: res.url,
            status: res.status,
            statusText: res.statusText,
            text,
            error,
        })
        throw new Error(`服务返回了无效响应（HTTP ${res.status}）`)
    }
}

async function getErrorMessage(res: Response, fallback: string): Promise<string> {
    try {
        const data = await parseJsonSafely<{ detail?: string; message?: string }>(res)
        return data?.detail || data?.message || fallback
    } catch (error) {
        if (error instanceof Error) {
            return error.message
        }
        return fallback
    }
}

async function getJsonOrThrow<T>(res: Response, fallback: string): Promise<T> {
    const data = await parseJsonSafely<T>(res)
    if (data === null) {
        throw new Error(fallback)
    }
    return data
}

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
    result_task_id: string | null
}

export interface BatchStatus {
    job_id: string | null
    session_id: string | null
    status: 'queued' | 'running' | 'stopping' | 'stopped' | 'completed' | 'failed' | 'interrupted'
    status_reason: 'normal' | 'stop_requested' | 'lease_expired' | 'reporter_timeout' | 'mineru_timeout' | 'llm_timeout' | 'worker_error'
    last_error: string | null
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
        if (!res.ok) {
            throw new Error('获取文件夹列表失败')
        }
        return getJsonOrThrow<FolderTreeResponse>(res, '获取文件夹列表失败')
    },

    async listPdfs(folderName: string): Promise<PdfInfo[]> {
        const res = await apiFetch(`${API_BASE}/batch/folders/${encodeURIComponent(folderName)}/pdfs`)
        if (!res.ok) {
            throw new Error('获取 PDF 列表失败')
        }
        const data = await getJsonOrThrow<{ pdfs: PdfInfo[] }>(res, '获取 PDF 列表失败')
        return data.pdfs
    },

    async start(folder: string, concurrency: number): Promise<BatchStatus> {
        const res = await apiFetch(`${API_BASE}/batch/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder, concurrency }),
        })
        if (!res.ok) {
            throw new Error(await getErrorMessage(res, '启动批量任务失败'))
        }
        return getJsonOrThrow<BatchStatus>(res, '启动批量任务失败')
    },

    async stop(jobId?: string | null): Promise<BatchStatus> {
        const targetUrl = jobId ? `${API_BASE}/batch/jobs/${encodeURIComponent(jobId)}/stop` : `${API_BASE}/batch/stop`
        const res = await apiFetch(targetUrl, {
            method: 'POST',
        })
        if (!res.ok) {
            throw new Error(await getErrorMessage(res, '停止批量任务失败'))
        }
        const data = await getJsonOrThrow<BatchStatus | { status: BatchStatus }>(res, '停止批量任务失败')
        return 'tasks' in data ? data : data.status
    },

    async getStatus(): Promise<BatchStatus> {
        const res = await apiFetch(`${API_BASE}/batch/status`)
        if (!res.ok) {
            throw new Error('获取批量任务状态失败')
        }
        return getJsonOrThrow<BatchStatus>(res, '获取批量任务状态失败')
    },

    async getJobStatus(jobId: string): Promise<BatchStatus> {
        const res = await apiFetch(`${API_BASE}/batch/jobs/${encodeURIComponent(jobId)}`)
        if (!res.ok) {
            throw new Error('获取批量任务状态失败')
        }
        return getJsonOrThrow<BatchStatus>(res, '获取批量任务状态失败')
    },

    async getLatestResumable(): Promise<ResumeLatestResponse> {
        const res = await apiFetch(`${API_BASE}/batch/jobs/latest-resumable`)
        if (!res.ok) {
            throw new Error('获取可继续任务失败')
        }
        return getJsonOrThrow<ResumeLatestResponse>(res, '获取可继续任务失败')
    },

    async resumeLatest(concurrency?: number): Promise<BatchStatus> {
        const res = await apiFetch(`${API_BASE}/batch/resume/latest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(concurrency ? { concurrency } : {}),
        })
        if (!res.ok) {
            throw new Error(await getErrorMessage(res, '继续批量任务失败'))
        }
        return getJsonOrThrow<BatchStatus>(res, '继续批量任务失败')
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
        if (!res.ok) {
            throw new Error(await getErrorMessage(res, '启动批量任务失败'))
        }
        return getJsonOrThrow<BatchStatus>(res, '启动批量任务失败')
    },

    async listSessions(): Promise<SessionInfo[]> {
        const res = await apiFetch(`${API_BASE}/batch/sessions`)
        if (!res.ok) {
            throw new Error('获取会话列表失败')
        }
        const data = await getJsonOrThrow<{ sessions: SessionInfo[] }>(res, '获取会话列表失败')
        return data.sessions
    },

    async restoreSession(sessionId: string): Promise<{ message: string; session_id: string; status: BatchStatus }> {
        const res = await apiFetch(`${API_BASE}/batch/restore/${encodeURIComponent(sessionId)}`, {
            method: 'POST',
        })
        if (!res.ok) {
            throw new Error(await getErrorMessage(res, '恢复会话失败'))
        }
        return getJsonOrThrow<{ message: string; session_id: string; status: BatchStatus }>(res, '恢复会话失败')
    },

    async deleteSession(sessionId: string): Promise<{ message: string }> {
        const res = await apiFetch(`${API_BASE}/batch/sessions/${encodeURIComponent(sessionId)}`, {
            method: 'DELETE',
        })
        if (!res.ok) {
            throw new Error(await getErrorMessage(res, '删除会话失败'))
        }
        return getJsonOrThrow<{ message: string }>(res, '删除会话失败')
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
