import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { authService, type AuthState } from '@/services/auth'
import { clearClientSessionState, subscribeUnauthorized } from '@/services/http'
import { AuthContext, type AuthContextValue } from './context'

function AuthLoadingScreen() {
    return (
        <div className="flex min-h-screen items-center justify-center bg-[linear-gradient(135deg,#f5f7fa_0%,#e4ecf7_100%)]">
            <div className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
                <Loader2 className="h-5 w-5 animate-spin text-slate-600" />
                <span className="text-sm font-medium text-slate-700">正在检查登录状态</span>
            </div>
        </div>
    )
}

export function AuthProvider({ children }: { children: ReactNode }) {
    const navigate = useNavigate()
    const location = useLocation()
    const locationRef = useRef(location)
    const [authState, setAuthState] = useState<AuthState>({ authenticated: false, user: null })
    const [isLoading, setIsLoading] = useState(true)

    useEffect(() => {
        locationRef.current = location
    }, [location])

    const refresh = useCallback(async () => {
        setIsLoading(true)
        try {
            const state = await authService.me()
            setAuthState(state)
        } finally {
            setIsLoading(false)
        }
    }, [])

    useEffect(() => {
        void refresh()
    }, [refresh])

    useEffect(() => {
        return subscribeUnauthorized(() => {
            clearClientSessionState()
            setAuthState({ authenticated: false, user: null })
            setIsLoading(false)

            const currentLocation = locationRef.current
            const currentPath = `${currentLocation.pathname}${currentLocation.search}${currentLocation.hash}`
            if (currentLocation.pathname !== '/login') {
                navigate('/login', {
                    replace: true,
                    state: { from: currentPath },
                })
            }
        })
    }, [navigate])

    const login = useCallback(async (username: string, password: string) => {
        const state = await authService.login({ username, password })
        setAuthState(state)
        return state
    }, [])

    const logout = useCallback(async () => {
        try {
            await authService.logout()
        } finally {
            clearClientSessionState()
            setAuthState({ authenticated: false, user: null })
            navigate('/login', { replace: true })
        }
    }, [navigate])

    const isAdmin = authState.user?.role === 'admin'

    const value = useMemo<AuthContextValue>(() => ({
        authenticated: authState.authenticated,
        isLoading,
        user: authState.user,
        isAdmin,
        login,
        logout,
        refresh,
    }), [authState.authenticated, authState.user, isLoading, isAdmin, login, logout, refresh])

    if (isLoading) {
        return <AuthLoadingScreen />
    }

    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
