/**
 * i18n 国际化配置
 *
 * 支持中英文切换，默认语言为英语
 */
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import zhCN from '../locales/zh-CN.json'
import enUS from '../locales/en-US.json'

// 语言资源
const resources = {
    'zh-CN': { translation: zhCN },
    'en-US': { translation: enUS }
}

i18n
    // 检测用户语言
    .use(LanguageDetector)
    // 将 i18n 实例传递给 react-i18next
    .use(initReactI18next)
    // 初始化 i18n
    .init({
        resources,
        fallbackLng: 'en-US', // 默认语言改为英语
        debug: false,
        interpolation: {
            escapeValue: false // React 已经安全处理了 XSS
        },
        detection: {
            // 语言检测顺序
            order: ['localStorage', 'navigator'],
            // 缓存到 localStorage
            caches: ['localStorage'],
            lookupLocalStorage: 'i18nextLng'
        }
    })

// 监听语言变化，更新 html lang 属性
i18n.on('languageChanged', (lng) => {
    document.documentElement.lang = lng
})

// 初始化时设置 html lang 属性
document.documentElement.lang = i18n.language

export default i18n
