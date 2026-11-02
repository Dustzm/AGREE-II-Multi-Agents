import { MessageView } from '@/components/MessageView'
import { asMessage, type UiMessage } from '@/i18n/messages'
import { FormEvent, useMemo, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AlertCircle, ArrowRight, Globe, Loader2, LockKeyhole, UserRound } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/auth/useAuth'
import { BrandSignature } from '@/components/BrandSignature'

function getRedirectTarget(value: unknown): string {
    if (typeof value !== 'string' || !value.startsWith('/')) {
        return '/chat'
    }
    return value
}

export function Login() {
    const navigate = useNavigate()
    const location = useLocation()
    const { i18n } = useTranslation()
    const { authenticated, login } = useAuth()
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState<UiMessage | null>(null)
    const [isSubmitting, setIsSubmitting] = useState(false)
    const isZh = i18n.language?.startsWith('zh')

    const copy = isZh ? {
        heroTitle: '像交付成品一样审阅指南。',
        loginTitle: '登录工作台',
        loginBody: '进入 AMA 评估界面。',
        username: '用户名',
        password: '密码',
        usernamePlaceholder: '请输入用户名',
        passwordPlaceholder: '请输入密码',
        submit: '进入 AMA',
        submitting: '登录中',
        error: '登录失败',
        switchLabel: 'EN',
        switchTitle: 'Switch to English',
    } : {
        heroTitle: 'Review guidelines like finished products.',
        loginTitle: 'Sign in to AMA',
        loginBody: 'Enter the AMA workspace.',
        username: 'Username',
        password: 'Password',
        usernamePlaceholder: 'Enter username',
        passwordPlaceholder: 'Enter password',
        submit: 'Enter AMA',
        submitting: 'Signing in',
        error: 'Sign-in failed',
        switchLabel: '中文',
        switchTitle: '切换到中文',
    }

    const redirectTo = useMemo(
        () => getRedirectTarget((location.state as { from?: unknown } | null)?.from),
        [location.state]
    )

    if (authenticated) {
        return <Navigate to="/chat" replace />
    }

    const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault()
        setError(null)
        setIsSubmitting(true)

        try {
            await login(username.trim(), password)
            navigate(redirectTo, { replace: true })
        } catch (submitError) {
            setError(asMessage(submitError, 'server.error.invalid_credentials'))
        } finally {
            setIsSubmitting(false)
        }
    }

    const toggleLanguage = () => {
        i18n.changeLanguage(isZh ? 'en-US' : 'zh-CN')
    }

    return (
        <div className="relative min-h-screen overflow-hidden bg-[#000000] text-white">
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_16%,rgba(0,113,227,0.22),transparent_22%),radial-gradient(circle_at_80%_26%,rgba(255,255,255,0.07),transparent_18%),radial-gradient(circle_at_52%_78%,rgba(0,113,227,0.12),transparent_24%)]" />
            <div className="absolute inset-x-0 top-0 h-px bg-white/12" />

            <div className="relative flex min-h-screen flex-col px-6 py-8 sm:px-10 lg:px-14">
                <header className="absolute right-6 top-6 sm:right-10 sm:top-8 z-10">
                    <button
                        type="button"
                        onClick={toggleLanguage}
                        className="inline-flex h-10 items-center gap-2 rounded-full border border-white/10 bg-white/6 px-4 text-sm font-medium text-white/78 transition-all hover:border-white/20 hover:bg-white/10 hover:text-white"
                        title={copy.switchTitle}
                        aria-label={copy.switchTitle}
                    >
                        <Globe className="h-4 w-4" />
                        <span className="font-mono text-[0.72rem] uppercase tracking-[0.18em]">
                            {copy.switchLabel}
                        </span>
                    </button>
                </header>

                <main className="flex flex-1 items-center justify-center py-12">
                    <div className="w-full max-w-[420px]">
                        <div className="flex flex-col items-center text-center">
                            <BrandSignature inverted className="mb-12 scale-[1.15] select-none flex-col items-center gap-6 text-center" />

                            <div className="w-full rounded-[28px] border border-white/10 bg-[rgba(245,245,247,0.96)] p-6 text-left shadow-[0_20px_60px_-20px_rgba(0,0,0,0.6)] backdrop-blur-[20px] sm:p-8">
                                <form className="space-y-6" onSubmit={handleSubmit}>
                                    <label className="block">
                                        <div className="flex items-center gap-3 rounded-[20px] border border-black/10 bg-white px-4 py-3 shadow-[0_12px_32px_-28px_rgba(0,0,0,0.42)] transition-colors focus-within:border-[#0071e3]">
                                            <UserRound className="h-4 w-4 text-black/36" />
                                            <input
                                                value={username}
                                                onChange={(event) => setUsername(event.target.value)}
                                                className="w-full border-0 bg-transparent text-[1rem] text-[#1d1d1f] outline-none placeholder:text-black/28"
                                                placeholder={copy.usernamePlaceholder}
                                                autoComplete="username"
                                                disabled={isSubmitting}
                                            />
                                        </div>
                                    </label>

                                    <label className="block">
                                        <div className="flex items-center gap-3 rounded-[20px] border border-black/10 bg-white px-4 py-3 shadow-[0_12px_32px_-28px_rgba(0,0,0,0.42)] transition-colors focus-within:border-[#0071e3]">
                                            <LockKeyhole className="h-4 w-4 text-black/36" />
                                            <input
                                                type="password"
                                                value={password}
                                                onChange={(event) => setPassword(event.target.value)}
                                                className="w-full border-0 bg-transparent text-[1rem] text-[#1d1d1f] outline-none placeholder:text-black/28"
                                                placeholder={copy.passwordPlaceholder}
                                                autoComplete="current-password"
                                                disabled={isSubmitting}
                                            />
                                        </div>
                                    </label>

                                    {error ? (
                                        <div className="flex items-start gap-2 rounded-[18px] border border-[#d12e2e]/16 bg-[#fff1f1] px-4 py-3 text-sm text-[#9f1c1c]">
                                            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                                            <MessageView message={error} />
                                        </div>
                                    ) : null}

                                    <Button
                                        type="submit"
                                        className="h-12 w-full rounded-full bg-[#0071e3] text-[1rem] font-normal text-white shadow-none hover:bg-[#0066cc]"
                                        disabled={isSubmitting || !username.trim() || !password}
                                    >
                                        {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
                                        {isSubmitting ? copy.submitting : copy.submit}
                                    </Button>
                                </form>
                            </div>
                        </div>
                    </div>
                </main>
            </div>
        </div>
    )
}
