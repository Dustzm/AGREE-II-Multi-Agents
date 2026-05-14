import { API_URL } from '@/config'

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
        const error = await response.json().catch(() => ({ detail: fallback }))
        throw new Error(error.detail || fallback)
    }

    return response.json() as Promise<AuthState>
}

export const authService = {
    async me(): Promise<AuthState> {
        const response = await fetch(`${API_URL}/auth/me`, {
            credentials: 'include',
        })
        return parseAuthResponse(response, '获取登录状态失败')
    },

    async login(payload: LoginPayload): Promise<AuthState> {
        const response = await fetch(`${API_URL}/auth/login`, {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        })
        return parseAuthResponse(response, '登录失败')
    },

    async logout(): Promise<AuthState> {
        const response = await fetch(`${API_URL}/auth/logout`, {
            method: 'POST',
            credentials: 'include',
        })
        return parseAuthResponse(response, '退出登录失败')
    },
}
