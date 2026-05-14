import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'

export function ProtectedRoute() {
    const { authenticated } = useAuth()
    const location = useLocation()

    if (!authenticated) {
        const from = `${location.pathname}${location.search}${location.hash}`
        return <Navigate to="/login" replace state={{ from }} />
    }

    return <Outlet />
}
