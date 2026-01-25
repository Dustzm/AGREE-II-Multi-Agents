const API_BASE = '/ama/api'

export interface FolderInfo {
    name: string
    path: string
    pdf_count: number
    modified: string
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
    status: 'pending' | 'running' | 'done' | 'error' | 'cancelled'
    retry_count: number
    error: string | null
    start_time: string | null
    end_time: string | null
    current_step: string | null
    result_task_id: string | null
}

export interface BatchStatus {
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
    tasks: BatchTask[]
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

export const batchApi = {
    async listFolders(): Promise<FolderInfo[]> {
        const res = await fetch(`${API_BASE}/batch/folders`)
        if (!res.ok) {
            throw new Error('获取文件夹列表失败')
        }
        const data = await res.json()
        return data.folders
    },

    async listPdfs(folderName: string): Promise<PdfInfo[]> {
        const res = await fetch(`${API_BASE}/batch/folders/${encodeURIComponent(folderName)}/pdfs`)
        if (!res.ok) {
            throw new Error('获取 PDF 列表失败')
        }
        const data = await res.json()
        return data.pdfs
    },

    async start(folder: string, concurrency: number): Promise<BatchStatus> {
        const res = await fetch(`${API_BASE}/batch/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder, concurrency }),
        })
        if (!res.ok) {
            const data = await res.json()
            throw new Error(data.detail || '启动批量任务失败')
        }
        return res.json()
    },

    async stop(): Promise<void> {
        const res = await fetch(`${API_BASE}/batch/stop`, {
            method: 'POST',
        })
        if (!res.ok) {
            const data = await res.json()
            throw new Error(data.detail || '停止批量任务失败')
        }
    },

    async getStatus(): Promise<BatchStatus> {
        const res = await fetch(`${API_BASE}/batch/status`)
        if (!res.ok) {
            throw new Error('获取批量任务状态失败')
        }
        return res.json()
    },

    async startSelected(pdfPaths: string[], concurrency: number, experimentRounds: number = 1): Promise<BatchStatus> {
        const res = await fetch(`${API_BASE}/batch/start-selected`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pdf_paths: pdfPaths,
                concurrency,
                experiment_rounds: experimentRounds
            }),
        })
        if (!res.ok) {
            const data = await res.json()
            throw new Error(data.detail || '启动批量任务失败')
        }
        return res.json()
    },

    async listSessions(): Promise<SessionInfo[]> {
        const res = await fetch(`${API_BASE}/batch/sessions`)
        if (!res.ok) {
            throw new Error('获取会话列表失败')
        }
        const data = await res.json()
        return data.sessions
    },

    async restoreSession(sessionId: string): Promise<{ message: string; session_id: string; status: BatchStatus }> {
        const res = await fetch(`${API_BASE}/batch/restore/${encodeURIComponent(sessionId)}`, {
            method: 'POST',
        })
        if (!res.ok) {
            const data = await res.json()
            throw new Error(data.detail || '恢复会话失败')
        }
        return res.json()
    },

    async deleteSession(sessionId: string): Promise<{ message: string }> {
        const res = await fetch(`${API_BASE}/batch/sessions/${encodeURIComponent(sessionId)}`, {
            method: 'DELETE',
        })
        if (!res.ok) {
            const data = await res.json()
            throw new Error(data.detail || '删除会话失败')
        }
        return res.json()
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
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ama/api/batch/ws/${sessionId}`
    
    const state = {
        reconnectAttempts: 0,
        maxReconnectAttempts: 5,
        reconnectTimeout: null as NodeJS.Timeout | null,
        heartbeatInterval: null as NodeJS.Timeout | null,
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
        if (state.reconnectTimeout) {
            clearTimeout(state.reconnectTimeout)
        }
        const delay = Math.min(1000 * Math.pow(2, state.reconnectAttempts), 30000)
        console.log(`批量任务 WebSocket 将在 ${delay}ms 后尝试重连 (尝试 ${state.reconnectAttempts}/${state.maxReconnectAttempts})`)
        onConnectionChange?.(false, state.reconnectAttempts + 1)
        
        state.reconnectTimeout = setTimeout(() => {
            if (state.reconnectAttempts < state.maxReconnectAttempts) {
                state.reconnectAttempts++
                const newWs = new WebSocket(wsUrl)
                setupWebSocketHandlers(newWs)
                state.ws = newWs
            } else {
                console.log('批量任务 WebSocket 已达到最大重连次数，停止重连')
                onConnectionChange?.(false, 0)
            }
        }, delay) as any
    }

    const setupWebSocketHandlers = (ws: WebSocket) => {
        ws.onopen = () => {
            console.log('批量任务 WebSocket 已连接')
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

        ws.onclose = () => {
            console.log('批量任务 WebSocket 已断开')
            stopHeartbeat()
            onConnectionChange?.(false, state.reconnectAttempts)
            
            if (state.reconnectAttempts < state.maxReconnectAttempts) {
                scheduleReconnect()
            }
        }
    }

    const reconnect = () => {
        console.log('手动触发批量任务 WebSocket 重连')
        if (state.ws) {
            state.ws.close()
        }
        state.reconnectAttempts = 0
        const newWs = new WebSocket(wsUrl)
        setupWebSocketHandlers(newWs)
        state.ws = newWs
    }

    const disconnect = () => {
        stopReconnect()
        stopHeartbeat()
        if (state.ws) {
            state.ws.close()
            state.ws = null
        }
    }

    const ws = new WebSocket(wsUrl)
    setupWebSocketHandlers(ws)
    state.ws = ws

    return {
        ws,
        reconnect,
        disconnect
    }
}
