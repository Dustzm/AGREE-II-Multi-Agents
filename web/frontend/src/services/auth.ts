import { API_URL } from '@/config'
import { languageFetch, readJson } from './http'
import { LocalizedError, responseMessage } from '@/i18n/messages'

export interface AuthUser {
    username: string
    display_name?: string | null
    role: 'admin' | 'user'
}

export interface AuthState {
    authenticated: boolean
    user: AuthUser | null
}

interface LoginPayload {
    username: string
    password: string
}

async function parseAuthResponse(response: Response, fallback: string): Promise<AuthState> {
    if (!response.ok) {
        const error = await readJson<Record<string, unknown>>(response)
        throw new LocalizedError(responseMessage(error, fallback))
    }

    return readJson<AuthState>(response)
}

export const authService = {
    async me(): Promise<AuthState> {
        const response = await languageFetch(`${API_URL}/auth/me`, {
            credentials: 'include',
        })
        return parseAuthResponse(response, 'error.unexpected')
    },

    async login(payload: LoginPayload): Promise<AuthState> {
        const response = await languageFetch(`${API_URL}/auth/login`, {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        })
        return parseAuthResponse(response, 'error.invalid_credentials')
    },

    async logout(): Promise<AuthState> {
        const response = await languageFetch(`${API_URL}/auth/logout`, {
            method: 'POST',
            credentials: 'include',
        })
        return parseAuthResponse(response, 'error.unexpected')
    },
}
