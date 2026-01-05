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
                "flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all",
                "text-gray-600 hover:text-gray-900 hover:bg-gray-100/80"
            )}
            title={currentLang === 'zh' ? 'Switch to English' : '切换到中文'}
        >
            <Globe className="h-4 w-4" />
            <span className="uppercase font-mono text-xs">
                {currentLang === 'zh' ? '中文' : 'EN'}
            </span>
        </button>
    )
}
