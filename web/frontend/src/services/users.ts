import { apiFetch, readJson, buildApiUrl } from './http'

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

export const usersService = {
    async getAll(): Promise<User[]> {
        const response = await apiFetch(buildApiUrl('/users'))
        return readJson<User[]>(response)
    },

    async create(payload: CreateUserPayload): Promise<User> {
        const response = await apiFetch(buildApiUrl('/users'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        return readJson<User>(response)
    },

    async update(username: string, payload: UpdateUserPayload): Promise<User> {
        const response = await apiFetch(buildApiUrl(`/users/${encodeURIComponent(username)}`), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        return readJson<User>(response)
    },

    async delete(username: string): Promise<void> {
        await apiFetch(buildApiUrl(`/users/${encodeURIComponent(username)}`), {
            method: 'DELETE',
        })

    },

    async changeMyPassword(payload: ChangePasswordPayload): Promise<void> {
        await apiFetch(buildApiUrl('/users/me/password'), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })

    },
}
