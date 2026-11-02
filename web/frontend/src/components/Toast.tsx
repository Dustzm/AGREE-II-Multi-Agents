/**
 * Toast 提示组件
 * 固定在页面顶部，自动消失的轻量级提示
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, XCircle, AlertCircle, Info, X } from 'lucide-react'
import { cn } from '@/utils/cn'
import { useTranslation } from 'react-i18next'
import { MessageView } from './MessageView'
import type { UiMessage } from '@/i18n/messages'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

interface ToastProps {
    type: ToastType
    message: UiMessage | Error | string
    duration?: number  // 显示时长（毫秒），默认 3000
    onClose?: () => void
}

const toastConfig = {
    success: {
        icon: CheckCircle2,
        color: 'text-green-600',
        bg: 'bg-green-50 border-green-200',
        iconBg: 'bg-green-100'
    },
    error: {
        icon: XCircle,
        color: 'text-red-600',
        bg: 'bg-red-50 border-red-200',
        iconBg: 'bg-red-100'
    },
    warning: {
        icon: AlertCircle,
        color: 'text-orange-500',
        bg: 'bg-orange-50 border-orange-200',
        iconBg: 'bg-orange-100'
    },
    info: {
        icon: Info,
        color: 'text-blue-600',
        bg: 'bg-blue-50 border-blue-200',
        iconBg: 'bg-blue-100'
    }
}

export function Toast({ type, message, duration = 3000, onClose }: ToastProps) {
    const { t } = useTranslation()
    const [isVisible, setIsVisible] = useState(true)
    const [isExiting, setIsExiting] = useState(false)
    const closeCallback = useRef(onClose)
    const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

    const config = toastConfig[type]
    const Icon = config.icon

    useEffect(() => { closeCallback.current = onClose }, [onClose])

    const handleClose = useCallback(() => {
        if (closeTimer.current) return
        setIsExiting(true)
        closeTimer.current = setTimeout(() => {
            setIsVisible(false)
            closeCallback.current?.()
        }, 300)
    }, [])

    useEffect(() => {
        // 自动消失
        const timer = setTimeout(() => {
            handleClose()
        }, duration)

        return () => {
            clearTimeout(timer)
            if (closeTimer.current) clearTimeout(closeTimer.current)
        }
    }, [duration, handleClose])

    if (!isVisible) return null

    return (
        <div className={cn(
            "fixed top-4 left-1/2 -translate-x-1/2 z-50",
            "min-w-[320px] max-w-md px-4 py-3 rounded-lg border shadow-lg",
            "flex items-center gap-3",
            config.bg,
            "transition-all duration-300",
            isExiting ? "opacity-0 -translate-y-2" : "opacity-100 translate-y-0"
        )}>
            {/* 图标 */}
            <div className={cn(
                "flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center",
                config.iconBg
            )}>
                <Icon className={cn("w-5 h-5", config.color)} />
            </div>

            {/* 消息内容 */}
            <div className="flex-1 min-w-0">
                <div className={cn("text-sm font-medium", config.color)}>
                    <MessageView message={message} />
                </div>
            </div>

            {/* 关闭按钮 */}
            <button
                aria-label={t('ui.close')}
                onClick={handleClose}
                className={cn(
                    "flex-shrink-0 p-1 rounded-md transition-colors",
                    "hover:bg-black/5",
                    config.color
                )}
            >
                <X className="w-4 h-4" />
            </button>
        </div>
    )
}
