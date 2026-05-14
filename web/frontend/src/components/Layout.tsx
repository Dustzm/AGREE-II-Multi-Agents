import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'

export function Layout() {
    return (
        <div className="flex h-screen w-full overflow-hidden bg-[#f5f5f7] text-[#1d1d1f]">
            <Sidebar />
            <main className="flex-1 overflow-hidden bg-[#f5f5f7]">
                <div className="flex h-full flex-col">
                    <div className="flex-1 overflow-auto">
                        <Outlet />
                    </div>
                </div>
            </main>
        </div>
    )
}
