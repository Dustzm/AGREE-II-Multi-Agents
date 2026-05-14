/**
 * LanguageSwitch - 语言切换组件
 *
 * 在侧边栏底部显示，支持中英文切换
 */
import { useTranslation } from 'react-i18next'
import { Globe } from 'lucide-react'
import { cn } from '@/utils/cn'

export function LanguageSwitch() {
    const { i18n } = useTranslation()

    const currentLang = i18n.language?.startsWith('zh') ? 'zh' : 'en'

    const toggleLanguage = () => {
        const newLang = currentLang === 'zh' ? 'en-US' : 'zh-CN'
        i18n.changeLanguage(newLang)
    }

    return (
        <button
            onClick={toggleLanguage}
            className={cn(
                'inline-flex h-9 items-center gap-2 rounded-full border border-white/10 px-3 text-sm font-medium transition-all',
                'bg-white/6 text-white/76 hover:border-white/20 hover:bg-white/10 hover:text-white'
            )}
            title={currentLang === 'zh' ? 'Switch to English' : '切换到中文'}
        >
            <Globe className="h-4 w-4" />
            <span className="font-mono text-[0.72rem] uppercase tracking-[0.18em]">
                {currentLang === 'zh' ? '中文' : 'EN'}
            </span>
        </button>
    )
}
