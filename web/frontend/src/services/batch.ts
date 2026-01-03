/**
 * 批量任务 API 服务
 */

const API_BASE = '/ama/api'

// ===== 数据类型 =====

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
    pending: number
    running: number
    done: number
    error: number
    cancelled: number
    tasks: BatchTask[]
}

// ===== API 函数 =====

export const batchApi = {
    /**
     * 获取 input 目录下的文件夹列表
     */
    async listFolders(): Promise<FolderInfo[]> {
        const res = await fetch(`${API_BASE}/batch/folders`)
        if (!res.ok) {
            throw new Error('获取文件夹列表失败')
        }
        const data = await res.json()
        return data.folders
    },

    /**
     * 获取指定文件夹下的 PDF 文件列表
     */
    async listPdfs(folderName: string): Promise<PdfInfo[]> {
        const res = await fetch(`${API_BASE}/batch/folders/${encodeURIComponent(folderName)}/pdfs`)
        if (!res.ok) {
            throw new Error('获取 PDF 列表失败')
        }
        const data = await res.json()
        return data.pdfs
    },

    /**
     * 启动批量任务
     */
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

    /**
     * 停止批量任务
     */
    async stop(): Promise<void> {
        const res = await fetch(`${API_BASE}/batch/stop`, {
            method: 'POST',
        })
        if (!res.ok) {
            const data = await res.json()
            throw new Error(data.detail || '停止批量任务失败')
        }
    },

    /**
     * 获取批量任务状态
     */
    async getStatus(): Promise<BatchStatus> {
        const res = await fetch(`${API_BASE}/batch/status`)
        if (!res.ok) {
            throw new Error('获取批量任务状态失败')
        }
        return res.json()
    },

    /**
     * 启动勾选 PDF 列表的批量任务
     */
    async startSelected(pdfPaths: string[], concurrency: number): Promise<BatchStatus> {
        const res = await fetch(`${API_BASE}/batch/start-selected`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pdf_paths: pdfPaths, concurrency }),
        })
        if (!res.ok) {
            const data = await res.json()
            throw new Error(data.detail || '启动批量任务失败')
        }
        return res.json()
    },
}

// ===== WebSocket 连接 =====

export function createBatchWebSocket(
    sessionId: string,
    onTaskUpdate: (task: BatchTask) => void,
    onComplete: (status: BatchStatus) => void,
    onError?: (error: string) => void
): WebSocket {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ama/api/batch/ws/${sessionId}`)

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data)
            if (data.type === 'task_update') {
                onTaskUpdate(data.task)
            } else if (data.type === 'batch_complete') {
                onComplete(data.status)
            } else if (data.type === 'status') {
                // 初始状态
                onComplete(data.status)
            }
        } catch (e) {
            console.error('WebSocket 消息解析失败:', e)
        }
    }

    ws.onerror = () => {
        onError?.('WebSocket 连接错误')
    }

    ws.onclose = () => {
        console.log('批量任务 WebSocket 已断开')
    }

    return ws
}
