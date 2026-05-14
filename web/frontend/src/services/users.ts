import { apiFetch, buildApiUrl } from './http'

export interface User {
    id: number
    username: string
    display_name: string
    role: 'admin' | 'user'
    enabled: boolean
    created_at: string
    updated_at: string
}

export interface CreateUserPayload {
    username: string
    password: string
    display_name?: string
    role?: 'admin' | 'user'
}

export interface UpdateUserPayload {
    password?: string
    display_name?: string
    role?: 'admin' | 'user'
    enabled?: boolean
}

export interface ChangePasswordPayload {
    old_password: string
    new_password: string
}

async function handleResponse<T>(response: Response, fallback: string): Promise<T> {
    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: fallback }))
        throw new Error(error.detail || fallback)
    }
    return response.json() as Promise<T>
}

export const usersService = {
    async getAll(): Promise<User[]> {
        const response = await apiFetch(buildApiUrl('/users'))
        return handleResponse<User[]>(response, '获取用户列表失败')
    },

    async create(payload: CreateUserPayload): Promise<User> {
        const response = await apiFetch(buildApiUrl('/users'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        return handleResponse<User>(response, '创建用户失败')
    },

    async update(username: string, payload: UpdateUserPayload): Promise<User> {
        const response = await apiFetch(buildApiUrl(`/users/${encodeURIComponent(username)}`), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        return handleResponse<User>(response, '更新用户失败')
    },

    async delete(username: string): Promise<void> {
        const response = await apiFetch(buildApiUrl(`/users/${encodeURIComponent(username)}`), {
            method: 'DELETE',
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: '删除用户失败' }))
            throw new Error(error.detail || '删除用户失败')
        }
    },

    async changeMyPassword(payload: ChangePasswordPayload): Promise<void> {
        const response = await apiFetch(buildApiUrl('/users/me/password'), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: '修改密码失败' }))
            throw new Error(error.detail || '修改密码失败')
        }
    },
}
