import { API_URL, WS_URL } from '@/config'

export class UnauthorizedError extends Error {
    constructor(message: string = '未登录或登录已失效') {
        super(message)
        this.name = 'UnauthorizedError'
    }
}

type UnauthorizedListener = () => void

const unauthorizedListeners = new Set<UnauthorizedListener>()

export function subscribeUnauthorized(listener: UnauthorizedListener): () => void {
    unauthorizedListeners.add(listener)
    return () => {
        unauthorizedListeners.delete(listener)
    }
}

export function notifyUnauthorized() {
    unauthorizedListeners.forEach((listener) => listener())
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
    const response = await fetch(input, {
        ...init,
        credentials: 'include',
    })

    if (response.status === 401) {
        notifyUnauthorized()
        throw new UnauthorizedError()
    }

    return response
}

export function createAuthenticatedWebSocket(path: string): WebSocket {
    return new WebSocket(`${WS_URL}${path}`)
}

export function isUnauthorizedClose(event: CloseEvent): boolean {
    return event.code === 1008 || event.reason === 'unauthorized'
}

export function clearClientSessionState() {
    const keys = [
        'ama_session_id',
        'ama_is_loading',
        'ama_task_phase',
        'ama_current_file',
        'ama_task_id',
        'ama_timeline_events',
        'ama_batch_summary',
        'ama_startup_time',
        'batch_session_id',
    ]

    keys.forEach((key) => {
        localStorage.removeItem(key)
        sessionStorage.removeItem(key)
    })
}

export function buildApiUrl(path: string): string {
    if (/^(https?:)?\/\//.test(path)) {
        return path
    }
    const normalized = path.startsWith('/') ? path : `/${path}`
    return `${API_URL}${normalized}`
}
