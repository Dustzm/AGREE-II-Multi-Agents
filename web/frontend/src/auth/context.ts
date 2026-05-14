import { createContext } from 'react'
import type { AuthState, AuthUser } from '@/services/auth'

export interface AuthContextValue {
    authenticated: boolean
    isLoading: boolean
    user: AuthUser | null
    isAdmin: boolean
    login: (username: string, password: string) => Promise<AuthState>
    logout: () => Promise<void>
    refresh: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)
