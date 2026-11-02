import { notifyError } from '@/i18n/notify'
import { NavLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { BarChart2, BookOpenText, FileText, LogOut, MessageSquare, PlayCircle, Settings, Sliders, Users } from 'lucide-react'
import { cn } from '@/utils/cn'
import { useAuth } from '@/auth/useAuth'
import { BrandSignature } from './BrandSignature'
import { LanguageSwitch } from './LanguageSwitch'
import { APP_BASE_PATH } from '@/config'

const navItems = [
    { to: '/chat', icon: MessageSquare, labelKey: 'nav.chat' },
    { to: '/batch', icon: PlayCircle, labelKey: 'nav.batch' },
    { to: '/benchmark', icon: BarChart2, labelKey: 'nav.benchmark' },
    { to: '/reports', icon: FileText, labelKey: 'nav.reports' },
    { to: '/skills', icon: Settings, labelKey: 'nav.skills' },
    { to: '/config', icon: Sliders, labelKey: 'nav.config' },
]

// 仅管理员可见的导航项
const adminNavItems = [
    { to: '/users', icon: Users, labelKey: 'nav.users' },
]

export function Sidebar() {
    const { t } = useTranslation()
    const { user, isAdmin, logout } = useAuth()

    const openDocs = () => {
        window.open(`${APP_BASE_PATH}/docs`, '_blank', 'noopener,noreferrer')
    }

    const renderNavItem = (item: typeof navItems[0] & { fallbackLabel?: string }) => (
        <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
                cn(
                    'group relative flex items-center gap-3 rounded-[18px] px-4 py-3.5 text-sm transition-all duration-200',
                    isActive
                        ? 'bg-white text-[#1d1d1f] shadow-[0_14px_36px_-26px_rgba(255,255,255,0.8)]'
                        : 'text-white/66 hover:bg-white/8 hover:text-white'
                )
            }
        >
            {({ isActive }) => (
                <>
                    <item.icon className={cn(
                        'h-4 w-4 shrink-0',
                        isActive ? 'text-[#0071e3]' : 'text-white/42 group-hover:text-white/72'
                    )} />
                    <span className="font-medium tracking-[-0.01em]">
                        {t(item.labelKey)}
                    </span>
                    {isActive ? <span className="ml-auto h-2 w-2 rounded-full bg-[#0071e3]" /> : null}
                </>
            )}
        </NavLink>
    )

    return (
        <aside className="relative flex h-screen w-[312px] shrink-0 flex-col overflow-hidden border-r border-white/8 bg-[rgba(0,0,0,0.84)] text-white shadow-[10px_0_40px_-24px_rgba(0,0,0,0.45)] backdrop-blur-[24px]">
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(41,151,255,0.16),transparent_24%),linear-gradient(180deg,rgba(255,255,255,0.05),rgba(255,255,255,0))]" />

            <div className="relative border-b border-white/4 px-6 pb-6 pt-8">
                <BrandSignature compact inverted />
            </div>

            <nav className="relative flex-1 px-4 py-8">
                <div className="space-y-1.5">
                    {navItems.map(renderNavItem)}
                </div>

                {/* 管理员专属导航 */}
                {isAdmin && (
                    <div className="mt-6">
                        <div className="mb-2 px-4 text-[0.62rem] font-medium uppercase tracking-[0.22em] text-white/24">
                            {t('users.sidebar.adminSection')}
                        </div>
                        <div className="space-y-1.5">
                            {adminNavItems.map(renderNavItem)}
                        </div>
                    </div>
                )}
            </nav>

            <div className="relative px-6 py-6 border-t border-white/4">
                <div className="text-[0.68rem] font-medium uppercase tracking-[0.22em] text-white/30 mb-3">
                    {t('ui.account')}
                </div>
                <div className="flex items-center gap-2">
                    <div className="text-sm font-medium tracking-[-0.01em] text-white">
                        {user?.display_name || user?.username || t('ui.unknownUser')}
                    </div>
                    {isAdmin && (
                        <span className="px-1.5 py-0.5 text-[9px] font-bold rounded-full bg-amber-500/20 text-amber-400 border border-amber-500/30 uppercase tracking-wider">
                            {t('users.sidebar.adminLabel')}
                        </span>
                    )}
                </div>
                <div className="mt-0.5 truncate text-xs text-white/46">{user?.username}</div>

                <div className="mt-4 flex items-center gap-2">
                    <button
                        type="button"
                        onClick={openDocs}
                        className="flex h-9 w-9 items-center justify-center rounded-full bg-white/6 text-white/68 transition-all hover:bg-white/10 hover:text-white"
                        title={t('docs.openInNewTab')}
                        aria-label={t('docs.openInNewTab')}
                    >
                        <BookOpenText className="h-4 w-4" />
                    </button>
                    <LanguageSwitch />
                    <button
                        type="button"
                        onClick={() => void logout().catch(notifyError)}
                        className="flex h-9 w-9 items-center justify-center rounded-full bg-white/6 text-white/68 transition-all hover:bg-white/10 hover:text-white"
                        title={t('sidebar.logout')}
                        aria-label={t('sidebar.logout')}
                    >
                        <LogOut className="h-4 w-4" />
                    </button>
                </div>
            </div>
        </aside>
    )
}
