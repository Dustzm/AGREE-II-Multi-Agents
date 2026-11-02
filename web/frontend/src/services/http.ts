import { API_URL, WS_URL } from '@/config'
import i18n from '@/i18n'
import { LocalizedError, responseMessage, type UiMessage } from '@/i18n/messages'

export class UnauthorizedError extends LocalizedError {
    constructor(data: UiMessage = { i18n: { key: 'error.unauthorized', params: {} } }) {
        super(data)
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
    const response = await languageFetch(input, init)

    if (response.status === 401) {
        notifyUnauthorized()
        throw new UnauthorizedError(responseMessage(await readJson(response), 'error.unauthorized'))
    }

    if (!response.ok) {
        throw new LocalizedError(responseMessage(await readJson(response)))
    }
    return response
}

/** Login uses the same transport without triggering protected-API logout on 401. */
export async function languageFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined))
    if (!headers.has('Accept-Language')) headers.set('Accept-Language', i18n.resolvedLanguage || i18n.language || 'en-US')
    try {
        return await fetch(input, { ...init, headers, credentials: 'include' })
    } catch (error) {
        throw new LocalizedError({ i18n: { key: 'error.network', params: {} }, diagnostic: error instanceof Error ? error.message : undefined })
    }
}

export async function readJson<T>(response: Response): Promise<T> {
    try {
        const body: unknown = await response.json()
        if (body === null || typeof body !== 'object') throw new TypeError('Invalid JSON response')
        return body as T
    } catch {
        throw new LocalizedError({ i18n: { key: 'error.invalid_response', params: {} }, diagnostic: `HTTP ${response.status}` })
    }
}

export function createAuthenticatedWebSocket(path: string): WebSocket {
    const url = new URL(`${WS_URL}${path}`)
    // Browser WebSockets cannot set Accept-Language headers.
    url.searchParams.set('lang', i18n.resolvedLanguage || i18n.language || 'en-US')
    return new WebSocket(url.toString())
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
