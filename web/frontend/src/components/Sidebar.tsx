import { NavLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { MessageSquare, FileText, Settings, Sparkles, Bot, PlayCircle } from 'lucide-react'
import { cn } from '@/utils/cn'
import { LanguageSwitch } from './LanguageSwitch'

const navItems = [
    { to: '/chat', icon: MessageSquare, labelKey: 'nav.chat' },
    { to: '/batch', icon: PlayCircle, labelKey: 'nav.batch' },
    { to: '/reports', icon: FileText, labelKey: 'nav.reports' },
    { to: '/skills', icon: Settings, labelKey: 'nav.skills' },
    { to: '/agents', icon: Bot, labelKey: 'nav.agents' },
]

export function Sidebar() {
    const { t } = useTranslation()

    return (
        <aside className="w-64 border-r border-gray-200/60 h-screen flex flex-col transition-all duration-300 relative overflow-hidden bg-white/95 shadow-[4px_0_24px_-12px_rgba(0,0,0,0.1)]" style={{ isolation: 'isolate' }}>
            <div className="absolute inset-0 bg-gradient-to-b from-white/60 to-white/10 pointer-events-none" />

            <div className="relative p-6 border-b border-white/20">
                <div className="flex items-center gap-3 mb-1">
                    <div className="p-2 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-xl text-white shadow-lg shadow-blue-500/30">
                        <Sparkles className="h-5 w-5" />
                    </div>
                    <div>
                        <h1 className="text-xl font-bold tracking-tight text-gray-800">AMA</h1>
                        <p className="text-[10px] text-gray-500 font-medium uppercase tracking-wider">{t('sidebar.subtitle')}</p>
                    </div>
                </div>
            </div>

            <nav className="relative flex-1 p-4 space-y-2">
                {navItems.map((item) => (
                    <NavLink
                        key={item.to}
                        to={item.to}
                        className={({ isActive }) =>
                            cn(
                                'flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all duration-300 group relative overflow-hidden',
                                isActive
                                    ? 'text-blue-600 shadow-md shadow-blue-500/10 bg-white/80'
                                    : 'text-gray-600 hover:bg-white/50 hover:text-gray-900'
                            )
                        }
                    >
                        {({ isActive }) => (
                            <>
                                <div className={cn(
                                    "absolute inset-0 bg-gradient-to-r from-blue-500/10 to-indigo-500/5 opacity-0 transition-opacity duration-300",
                                    isActive && "opacity-100"
                                )} />
                                <item.icon className={cn(
                                    "h-4 w-4 transition-all duration-300",
                                    isActive ? "text-blue-600 scale-110" : "text-gray-400 group-hover:text-gray-600"
                                )} />
                                <span className="relative">{t(item.labelKey)}</span>
                                {isActive && (
                                    <div className="absolute right-3 w-1.5 h-1.5 rounded-full bg-blue-500" />
                                )}
                            </>
                        )}
                    </NavLink>
                ))}
            </nav>

            <div className="relative p-4 border-t border-white/20">
                <div className="flex items-center justify-between text-xs text-gray-500 px-2">
                    <span className="font-mono opacity-70">v1.0.0</span>
                    <div className="flex items-center gap-3">
                        <LanguageSwitch />
                        <div className="flex items-center gap-1.5">
                            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                            <span className="opacity-70">{t('sidebar.status.online')}</span>
                        </div>
                    </div>
                </div>
            </div>
        </aside>
    )
}

