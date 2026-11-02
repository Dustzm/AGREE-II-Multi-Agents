import { MessageView } from '@/components/MessageView'
import { asMessage } from '@/i18n/messages'
import { notifyError, notifySuccess } from '@/i18n/notify'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, Wifi, WifiOff, Heart, Loader2, Eye, EyeOff, CheckCircle, XCircle, Clock, Sliders, AlertCircle, Zap, Server, Globe, Sparkles, BrainCircuit, Telescope, Sparkle, ChevronDown, Info, RotateCcw } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { api, ConnectionTestResult, HealthCheckResult, LLMConfigUpdate, ProviderInfo, ProviderModelInfo } from '@/services/api'
import { Button } from '@/components/ui/button'
import { cn } from '@/utils/cn'

type TestStatus = 'idle' | 'testing' | 'success' | 'error'
type HealthStatus = 'idle' | 'checking' | 'healthy' | 'unhealthy'

// 图标和配色映射（固定部分，文本从 providersData 读）
const PROVIDER_UI: Record<string, { icon: typeof Server; color: string; bg: string }> = {
    vllm: { icon: Server, color: 'text-emerald-500', bg: 'bg-emerald-50' },
    openai_compatible: { icon: Globe, color: 'text-teal-500', bg: 'bg-teal-50' },
    openai: { icon: Sparkles, color: 'text-teal-600', bg: 'bg-teal-50' },
    anthropic: { icon: BrainCircuit, color: 'text-orange-500', bg: 'bg-orange-50' },
    deepseek: { icon: Telescope, color: 'text-indigo-500', bg: 'bg-indigo-50' },
    gemini: { icon: Sparkle, color: 'text-sky-500', bg: 'bg-sky-50' },
    dashscope: { icon: Zap, color: 'text-violet-500', bg: 'bg-violet-50' },
    zhipu: { icon: Globe, color: 'text-cyan-500', bg: 'bg-cyan-50' },
}
const DEFAULT_UI = { icon: Globe, color: 'text-gray-500', bg: 'bg-gray-50' }

export function Config() {
    const { t } = useTranslation()

    // 表单状态
    const [provider, setProvider] = useState('vllm')
    const [isProviderOpen, setIsProviderOpen] = useState(false)
    const providerSelectRef = useRef<HTMLDivElement>(null)
    const [isModelOpen, setIsModelOpen] = useState(false)
    const modelSelectRef = useRef<HTMLDivElement>(null)
    const [baseUrlDrafts, setBaseUrlDrafts] = useState<Record<string, string>>({})
    const [apiKey, setApiKey] = useState('')
    const [apiKeyMasked, setApiKeyMasked] = useState('')
    const [modelName, setModelName] = useState('')
    const [contextWindow, setContextWindow] = useState('')
    const [contextWindowEdited, setContextWindowEdited] = useState(false)
    const [contextHint, setContextHint] = useState('')
    const [limitsLoading, setLimitsLoading] = useState(false)
    const limitsRevision = useRef(0)
    const loadedIdentity = useRef('')
    const loadedContextWindow = useRef('')
    const [timeout, setTimeout_] = useState(120)
    const [showApiKey, setShowApiKey] = useState(false)
    const [apiKeyEdited, setApiKeyEdited] = useState(false)
    const [isVisionModel, setIsVisionModel] = useState(true)

    // 提供商预设数据
    const [providersData, setProvidersData] = useState<Record<string, ProviderInfo>>({})

    // 当前配置来源：有值时表示来自管理台保存的运行时配置，否则来自 .env
    const [runtimeSource, setRuntimeSource] = useState<{ updatedAt: string | null; updatedBy: string | null } | null>(null)
    const [resetting, setResetting] = useState(false)

    const getBaseUrlValue = (
        providerId: string,
        drafts: Record<string, string> = baseUrlDrafts,
        providers: Record<string, ProviderInfo> = providersData
    ) => {
        if (Object.prototype.hasOwnProperty.call(drafts, providerId)) {
            return drafts[providerId]
        }
        return providers[providerId]?.default_base_url || ''
    }

    // 加载 / 保存状态
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)

    // 连接测试状态
    const [testStatus, setTestStatus] = useState<TestStatus>('idle')
    const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null)

    // 健康验证状态
    const [healthStatus, setHealthStatus] = useState<HealthStatus>('idle')
    const [healthResult, setHealthResult] = useState<HealthCheckResult | null>(null)

    // 点击外部关闭下拉框
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (providerSelectRef.current && !providerSelectRef.current.contains(event.target as Node)) {
                setIsProviderOpen(false)
            }
            if (modelSelectRef.current && !modelSelectRef.current.contains(event.target as Node)) {
                setIsModelOpen(false)
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [])

    // 加载配置
    const loadConfig = useCallback(async () => {
        try {
            setLoading(true)
            const [config, providers] = await Promise.all([
                api.getLLMConfig(),
                api.getProviders(),
            ])
            const nextProvider = config.provider || 'vllm'
            const nextBaseUrlDrafts = Object.fromEntries(
                Object.entries(providers).map(([providerId, providerInfo]) => [
                    providerId,
                    providerId === nextProvider
                        ? (config.base_url || providerInfo.default_base_url || '')
                        : (providerInfo.default_base_url || ''),
                ])
            )
            loadedIdentity.current = config.context_window == null ? '' : JSON.stringify([nextProvider, config.model_name, nextBaseUrlDrafts[nextProvider] || ''])
            loadedContextWindow.current = config.context_window == null ? '' : String(config.context_window)
            limitsRevision.current += 1
            setContextWindow(loadedContextWindow.current)
            setContextWindowEdited(false)
            setContextHint('')
            setLimitsLoading(false)
            setProvider(nextProvider)
            setBaseUrlDrafts(nextBaseUrlDrafts)
            setApiKeyMasked(config.api_key_masked)
            setModelName(config.model_name)
            setTimeout_(config.timeout)
            setIsVisionModel(config.is_vision_model)
            setApiKey('')
            setApiKeyEdited(false)
            setProvidersData(providers)
            setRuntimeSource(
                (config.runtime_source_fields?.length ?? 0) > 0
                    ? {
                        updatedAt: config.runtime_updated_at ?? null,
                        updatedBy: config.runtime_updated_by ?? null,
                    }
                    : null
            )
        } catch (error) {
            notifyError(error, 'config.toast.loadFailed')
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        loadConfig()
    }, [loadConfig])

    const baseUrl = getBaseUrlValue(provider)
    useEffect(() => {
        const identity = JSON.stringify([provider, modelName, baseUrl])
        if (loading) return
        const revision = ++limitsRevision.current
        if (identity === loadedIdentity.current) {
            setContextWindow(loadedContextWindow.current)
            setContextWindowEdited(false)
            setContextHint('')
            setLimitsLoading(false)
            return
        }
        setContextWindow('')
        setContextHint('')
        setLimitsLoading(true)
        const timer = window.setTimeout(() => {
            api.getModelLimits(provider, modelName).then(result => {
                if (limitsRevision.current !== revision) return
                const suggested = result.context_window == null ? '' : String(result.context_window)
                setContextWindow(suggested)
                // 页面展示的推荐窗口就是保存值；为空时不提交，让后端回落到 .env
                setContextWindowEdited(suggested !== '')
                setContextHint(result.status === 'matched'
                    ? t('config.llm.contextSuggested', { date: result.snapshot_at.slice(0, 10) })
                    : t(`config.llm.context${result.status === 'ambiguous' ? 'Ambiguous' : 'Unknown'}`))
            }).catch(() => {
                if (limitsRevision.current === revision) setContextHint(t('config.llm.contextLookupFailed'))
            }).finally(() => {
                if (limitsRevision.current === revision) setLimitsLoading(false)
            })
        }, 300)
        return () => { window.clearTimeout(timer); limitsRevision.current += 1 }
    }, [provider, modelName, baseUrl, loading, t])

    const validContext = contextWindow === '' || (/^[1-9]\d*$/.test(contextWindow) && Number.isSafeInteger(Number(contextWindow)))

    // SQLite datetime('now') 使用 UTC；转成浏览器本地时间展示
    const formatRuntimeTime = (value: string | null) => {
        if (!value) return ''
        const parsed = new Date(value.includes('T') ? value : `${value.replace(' ', 'T')}Z`)
        return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
    }

    // 恢复 .env 配置：清除管理台保存的运行时配置
    const handleReset = async () => {
        if (resetting) return
        if (!window.confirm(t('config.llm.resetConfirm'))) return
        setResetting(true)
        try {
            await api.resetLLMConfig()
            notifySuccess('config.toast.resetDone')
            await loadConfig()
            setTestStatus('idle')
            setTestResult(null)
            setHealthStatus('idle')
            setHealthResult(null)
        } catch (e: unknown) {
            notifyError(e, 'config.toast.resetFailed')
        } finally {
            setResetting(false)
        }
    }

    // 保存配置
    const handleSave = async () => {
        if (saving || !validContext || limitsLoading) return
        setSaving(true)
        try {
            const update: LLMConfigUpdate = {
                provider: provider,
                model_name: modelName,
                timeout: timeout,
                is_vision_model: isVisionModel,
            }
            if (contextWindowEdited) {
                update.context_window = contextWindow === '' ? null : Number(contextWindow)
            }
            const providerCfg = providersData[provider]
            if (providerCfg?.need_base_url) {
                update.base_url = getBaseUrlValue(provider)
            }
            if (apiKeyEdited) {
                update.api_key = apiKey
            }
            await api.updateLLMConfig(update)
            notifySuccess('config.toast.saved')
            await loadConfig()
            setTestStatus('idle')
            setTestResult(null)
            setHealthStatus('idle')
            setHealthResult(null)
        } catch (e: unknown) {
            notifyError(e, 'config.toast.saveFailed')
        } finally {
            setSaving(false)
        }
    }

    // 测试连接
    const handleTestConnection = async () => {
        setTestStatus('testing')
        setTestResult(null)
        try {
            const draftUpdate: LLMConfigUpdate = {
                provider,
                model_name: modelName,
                timeout,
                is_vision_model: isVisionModel,
            }
            const providerCfg = providersData[provider]
            if (providerCfg?.need_base_url) {
                draftUpdate.base_url = getBaseUrlValue(provider)
            }
            if (apiKeyEdited) {
                draftUpdate.api_key = apiKey
            }
            const result = await api.testLLMConnection(draftUpdate)
            setTestResult(result)
            setTestStatus(result.success ? 'success' : 'error')
        } catch (e) {
            setTestStatus('error')
            setTestResult({ success: false, message: '', ...asMessage(e, 'config.toast.saveFailed') })
        }
    }

    // 健康验证
    const handleHealthCheck = async () => {
        setHealthStatus('checking')
        setHealthResult(null)
        try {
            const draftUpdate: LLMConfigUpdate = {
                provider,
                model_name: modelName,
                timeout,
                is_vision_model: isVisionModel,
            }
            const providerCfg = providersData[provider]
            if (providerCfg?.need_base_url) {
                draftUpdate.base_url = getBaseUrlValue(provider)
            }
            if (apiKeyEdited) {
                draftUpdate.api_key = apiKey
            }
            const result = await api.checkLLMHealth(draftUpdate)
            setHealthResult(result)
            setHealthStatus(result.success ? 'healthy' : 'unhealthy')
        } catch (e) {
            setHealthStatus('unhealthy')
            setHealthResult({ success: false, message: '', ...asMessage(e, 'config.toast.saveFailed') })
        }
    }

    if (loading) return (
        <div className="flex h-full items-center justify-center p-8 bg-gray-50/50">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-teal-600/80"></div>
        </div>
    )

    return (
        <div className="h-full w-full overflow-y-auto bg-[#f5f5f7] p-8 space-y-8" style={{ overscrollBehavior: 'contain', willChange: 'scroll-position' }}>
            {/* 页面标题 */}
            <header className="mb-8 relative z-10">
                <div className="mb-3 text-[0.72rem] font-medium uppercase tracking-[0.22em] text-black/36">
                    {t('ui.modelOperations')}
                </div>
                <h2 className="mb-2 text-4xl font-semibold tracking-[-0.045em] text-[#1d1d1f]">
                    {t('config.title')}
                </h2>
                <p className="max-w-2xl text-[1.02rem] leading-[1.55] text-black/58">
                    {t('config.subtitle')}
                </p>
            </header>

            {/* LLM 服务配置无框扁平布局 */}
            <div className="relative w-full max-w-2xl">
                <div>
                    {/* 卡片标题 */}
                    <div className="flex items-center gap-3 mb-6">
                        <div className="rounded-2xl border border-black/6 bg-white p-2 shadow-[0_16px_32px_-26px_rgba(0,0,0,0.25)]">
                            <Sliders className="h-5 w-5 text-[#0071e3]" />
                        </div>
                        <h3 className="text-xl font-semibold tracking-[-0.03em] text-[#1d1d1f]">
                            {t('config.llm.title')}
                        </h3>
                    </div>

                    {/* 配置来源提示：说明当前生效值来自管理台保存还是 .env */}
                    <div className="mb-5 flex flex-wrap items-center gap-2 text-xs text-black/45">
                        <Info className="h-3.5 w-3.5 shrink-0" />
                        <span>
                            {runtimeSource
                                ? t('config.llm.runtimeSource', {
                                    time: formatRuntimeTime(runtimeSource.updatedAt) || '—',
                                    by: runtimeSource.updatedBy || '—',
                                })
                                : t('config.llm.envSource')}
                        </span>
                        {runtimeSource && (
                            <button
                                type="button"
                                onClick={handleReset}
                                disabled={resetting}
                                className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-xs text-gray-600 transition-colors hover:border-teal-300 hover:text-teal-700 disabled:opacity-50"
                            >
                                {resetting ? (
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                ) : (
                                    <RotateCcw className="h-3 w-3" />
                                )}
                                {t('config.llm.resetToEnv')}
                            </button>
                        )}
                    </div>

                    <div className="space-y-5">
                        {/* Provider */}
                        <div className="space-y-1.5 max-w-sm relative" ref={providerSelectRef}>
                            <label className="text-sm font-medium text-gray-700">{t('config.llm.provider')}</label>
                            <button
                                type="button"
                                onClick={() => setIsProviderOpen(!isProviderOpen)}
                                className="flex h-10 w-full items-center justify-between rounded-xl border border-gray-200 bg-white/50 px-4 py-2 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-teal-600/30 focus:border-teal-400 hover:bg-white"
                            >
                                <div className="flex items-center gap-2">
                                    {(() => {
                                        const ui = PROVIDER_UI[provider] || DEFAULT_UI
                                        const Icon = ui.icon
                                        const label = t(`config.llm.providers.${provider}`, {
                                            defaultValue: providersData[provider]?.name || provider,
                                        })
                                        return (
                                            <>
                                                <div className={`p-1 rounded-md ${ui.bg}`}>
                                                    <Icon className={`h-4 w-4 ${ui.color}`} />
                                                </div>
                                                <span className="font-medium text-gray-700">{label}</span>
                                            </>
                                        )
                                    })()}
                                </div>
                                <ChevronDown className={`h-4 w-4 text-gray-400 transition-transform ${isProviderOpen ? 'rotate-180' : ''}`} />
                            </button>

                            <AnimatePresence>
                                {isProviderOpen && (
                                    <motion.div
                                        initial={{ opacity: 0, y: -10, scale: 0.95 }}
                                        animate={{ opacity: 1, y: 0, scale: 1 }}
                                        exit={{ opacity: 0, y: -10, scale: 0.95 }}
                                        transition={{ duration: 0.15 }}
                                        className="absolute z-50 mt-2 w-full origin-top rounded-xl border border-gray-100 bg-white p-1.5 shadow-lg shadow-gray-200/50"
                                    >
                                        {Object.entries(providersData).map(([pid, pdata]) => {
                                            const ui = PROVIDER_UI[pid] || DEFAULT_UI
                                            const Icon = ui.icon
                                            const isSelected = pid === provider
                                            return (
                                                <button
                                                    key={pid}
                                                    type="button"
                                                    onClick={() => {
                                                        setProvider(pid)
                                                        setIsProviderOpen(false)
                                                    }}
                                                    className={`mr-0 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm transition-colors ${isSelected
                                                        ? 'bg-teal-50 text-teal-700'
                                                        : 'text-gray-600 hover:bg-gray-50'
                                                        }`}
                                                >
                                                    <div className={`p-1 flex items-center justify-center rounded-md ${isSelected ? 'bg-white shadow-sm' : ui.bg}`}>
                                                        <Icon className={`h-4 w-4 ${isSelected ? 'text-teal-600' : ui.color}`} />
                                                    </div>
                                                    <span className={isSelected ? 'font-semibold' : 'font-medium'}>
                                                        {t(`config.llm.providers.${pid}`, { defaultValue: pdata.name })}
                                                    </span>
                                                    {isSelected && <CheckCircle className="ml-auto h-4 w-4 text-teal-600" />}
                                                </button>
                                            )
                                        })}
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </div>

                        {/* Base URL（只要提供商需要就显示，默认值由 providers.json 预填） */}
                        {providersData[provider]?.need_base_url && (
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-gray-700">{t('config.llm.baseUrl')}</label>
                                <input
                                    type="text"
                                    value={getBaseUrlValue(provider)}
                                    onChange={(e) => {
                                        const nextBaseUrl = e.target.value
                                        setBaseUrlDrafts((current) => ({
                                            ...current,
                                            [provider]: nextBaseUrl,
                                        }))
                                    }}
                                    placeholder={'http://localhost:8080/v1'}
                                    className="flex h-10 w-full rounded-xl border border-gray-200 bg-white/50 px-4 py-2 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-teal-600/30 focus:border-teal-400"
                                />
                            </div>
                        )}

                        {/* API Key */}
                        <div className="space-y-1.5">
                            <label className="text-sm font-medium text-gray-700">{t('config.llm.apiKey')}</label>
                            <div className="relative">
                                <input
                                    type={showApiKey ? 'text' : 'password'}
                                    value={apiKeyEdited ? apiKey : apiKeyMasked}
                                    onChange={(e) => {
                                        setApiKey(e.target.value)
                                        setApiKeyEdited(true)
                                    }}
                                    onFocus={() => {
                                        if (!apiKeyEdited) {
                                            setApiKey('')
                                            setApiKeyEdited(true)
                                        }
                                    }}
                                    placeholder={t('config.llm.apiKeyPlaceholder')}
                                    className="flex h-10 w-full rounded-xl border border-gray-200 bg-white/50 px-4 py-2 pr-10 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-teal-600/30 focus:border-teal-400"
                                />
                                <button
                                    type="button"
                                    onClick={() => setShowApiKey(!showApiKey)}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
                                >
                                    {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                </button>
                            </div>
                            {apiKeyEdited && (
                                <p className="text-xs text-amber-500 flex items-center gap-1">
                                    <AlertCircle className="h-3 w-3" />
                                    {t('config.llm.apiKeyEdited')}
                                </p>
                            )}
                        </div>

                        {/* Model Name (自定义下拉 + 可输入) */}
                        <div className="space-y-1.5 max-w-sm relative" ref={modelSelectRef}>
                            <label className="text-sm font-medium text-gray-700">{t('config.llm.modelName')}</label>
                            <div className="relative">
                                <input
                                    type="text"
                                    value={modelName}
                                    onChange={(e) => {
                                        const val = e.target.value
                                        setModelName(val)
                                        const currentModels = providersData[provider]?.models || []
                                        const matched = currentModels.find((m: ProviderModelInfo) => m.id === val)
                                        if (matched !== undefined) {
                                            setIsVisionModel(matched.vision)
                                        }
                                    }}
                                    onFocus={() => {
                                        if ((providersData[provider]?.models || []).length > 0) {
                                            setIsModelOpen(true)
                                        }
                                    }}
                                    placeholder={
                                        providersData[provider]?.models?.[0]?.id || t('config.llm.modelNamePlaceholder')
                                    }
                                    className="flex h-10 w-full rounded-xl border border-gray-200 bg-white/50 px-4 py-2 pr-9 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-teal-600/30 focus:border-teal-400"
                                />
                                {(providersData[provider]?.models || []).length > 0 && (
                                    <button
                                        type="button"
                                        onClick={() => setIsModelOpen(!isModelOpen)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
                                    >
                                        <ChevronDown className={`h-4 w-4 transition-transform ${isModelOpen ? 'rotate-180' : ''}`} />
                                    </button>
                                )}
                            </div>

                            <AnimatePresence>
                                {isModelOpen && (providersData[provider]?.models || []).length > 0 && (
                                    <motion.div
                                        initial={{ opacity: 0, y: -6, scale: 0.97 }}
                                        animate={{ opacity: 1, y: 0, scale: 1 }}
                                        exit={{ opacity: 0, y: -6, scale: 0.97 }}
                                        transition={{ duration: 0.12 }}
                                        className="absolute z-50 mt-1 w-full origin-top rounded-xl border border-gray-100 bg-white p-1.5 shadow-lg shadow-gray-200/50"
                                    >
                                        {(providersData[provider]?.models || []).map((m: ProviderModelInfo) => {
                                            const isSelected = m.id === modelName
                                            return (
                                                <button
                                                    key={m.id}
                                                    type="button"
                                                    onClick={() => {
                                                        setModelName(m.id)
                                                        setIsVisionModel(m.vision)
                                                        setIsModelOpen(false)
                                                    }}
                                                    className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-sm transition-colors ${isSelected
                                                        ? 'bg-teal-50 text-teal-700'
                                                        : 'text-gray-600 hover:bg-gray-50'
                                                        }`}
                                                >
                                                    <span className={isSelected ? 'font-semibold' : 'font-medium'}>{m.id}</span>
                                                    <div className="flex items-center gap-1.5">
                                                        {m.vision && (
                                                            <div className="flex h-5 items-center justify-center rounded bg-teal-50 px-1.5" title={t('config.llm.visionModel')}>
                                                                <Eye className="h-3.5 w-3.5 text-teal-600/80" />
                                                            </div>
                                                        )}
                                                        {isSelected && <CheckCircle className="h-4 w-4 text-teal-600" />}
                                                    </div>
                                                </button>
                                            )
                                        })}
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </div>

                        <div className="space-y-1.5 max-w-sm">
                            <label htmlFor="context-window" className="text-sm font-medium text-gray-700">{t('config.llm.contextWindow')}</label>
                            <input id="context-window" type="number" min="1" step="1" value={contextWindow}
                                aria-invalid={!validContext} aria-describedby="context-window-hint"
                                onChange={event => {
                                    limitsRevision.current += 1
                                    setLimitsLoading(false)
                                    setContextHint('')
                                    setContextWindowEdited(true)
                                    setContextWindow(event.target.value)
                                }}
                                className="flex h-10 w-full rounded-xl border border-gray-200 px-4 text-sm focus:outline-none focus:ring-2 focus:ring-teal-600/30" />
                            <p id="context-window-hint" aria-live="polite" className="text-xs text-gray-500">
                                {!validContext ? t('config.llm.contextInvalid') : limitsLoading ? t('config.llm.contextLoading') : contextHint || t('config.llm.contextHint')}
                            </p>
                        </div>

                        {/* 视觉能力开关 */}
                        {(() => {
                            const isPredefinedModel = (providersData[provider]?.models || []).some(
                                (m: ProviderModelInfo) => m.id === modelName
                            )
                            return (
                                <div className={`flex items-center justify-between max-w-sm p-3.5 rounded-xl border transition-colors ${isPredefinedModel
                                    ? 'bg-gray-50/50 border-gray-100'
                                    : 'bg-white border-teal-100 shadow-sm'
                                    }`}>
                                    <div className="flex items-center gap-3">
                                        <div className={`p-2 rounded-lg ${isVisionModel ? 'bg-teal-50 text-teal-600' : 'bg-gray-100 text-gray-400'}`}>
                                            {isVisionModel ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
                                        </div>
                                        <div>
                                            <p className={`text-sm font-medium ${isPredefinedModel ? 'text-gray-500' : 'text-gray-700'}`}>
                                                {t('config.llm.visionModel')}
                                            </p>
                                            <p className="text-xs text-gray-400 mt-0.5">
                                                {isPredefinedModel
                                                    ? t('config.llm.visionModelLocked')
                                                    : t('config.llm.visionModelHint')
                                                }
                                            </p>
                                        </div>
                                    </div>
                                    <button
                                        type="button"
                                        disabled={isPredefinedModel}
                                        onClick={() => !isPredefinedModel && setIsVisionModel(!isVisionModel)}
                                        className={`relative inline-flex h-5 w-9 shrink-0 ${isPredefinedModel ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'
                                            } items-center rounded-full border-2 border-transparent transition-colors focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-2 ${isVisionModel ? 'bg-teal-600' : 'bg-gray-300'
                                            }`}
                                    >
                                        <span
                                            className={`pointer-events-none block h-4 w-4 rounded-full bg-white shadow-sm ring-0 transition-transform ${isVisionModel ? 'translate-x-4' : 'translate-x-0'
                                                }`}
                                        />
                                    </button>
                                </div>
                            )
                        })()}

                        {/* 操作按钮 */}
                        <div className="flex items-center gap-3 pt-6">
                            <Button
                                onClick={handleSave}
                                disabled={saving || !validContext || limitsLoading}
                                className="bg-teal-600 hover:bg-teal-700 text-white shadow-sm rounded-xl px-5"
                            >
                                {saving ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    <Save className="mr-2 h-4 w-4" />
                                )}
                                {saving ? t('config.button.saving') : t('config.button.save')}
                            </Button>
                            <Button
                                variant="outline"
                                onClick={handleTestConnection}
                                disabled={testStatus === 'testing'}
                                className={cn(
                                    "rounded-xl border-gray-200 hover:border-teal-300 hover:bg-teal-50/50",
                                    testStatus === 'success' && "border-green-200 text-green-700 bg-green-50/50",
                                    testStatus === 'error' && "border-red-200 text-red-700 bg-red-50/50"
                                )}
                            >
                                {testStatus === 'testing' ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : testStatus === 'success' ? (
                                    <CheckCircle className="mr-2 h-4 w-4 text-green-500" />
                                ) : testStatus === 'error' ? (
                                    <XCircle className="mr-2 h-4 w-4 text-red-500" />
                                ) : (
                                    <Wifi className="mr-2 h-4 w-4" />
                                )}
                                {t('config.button.testConnection')}
                            </Button>
                            <Button
                                variant="outline"
                                onClick={handleHealthCheck}
                                disabled={healthStatus === 'checking'}
                                className={cn(
                                    "rounded-xl border-gray-200 hover:border-teal-300 hover:bg-teal-50/50",
                                    healthStatus === 'healthy' && "border-green-200 text-green-700 bg-green-50/50",
                                    healthStatus === 'unhealthy' && "border-red-200 text-red-700 bg-red-50/50"
                                )}
                            >
                                {healthStatus === 'checking' ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : healthStatus === 'healthy' ? (
                                    <Heart className="mr-2 h-4 w-4 text-green-500 fill-green-500" />
                                ) : healthStatus === 'unhealthy' ? (
                                    <Heart className="mr-2 h-4 w-4 text-red-500" />
                                ) : (
                                    <Heart className="mr-2 h-4 w-4" />
                                )}
                                {t('config.button.healthCheck')}
                            </Button>
                        </div>
                    </div>
                </div>
            </div>

            {/* 连接测试结果 */}
            {testResult && (
                <div className={cn(
                    "relative overflow-hidden rounded-2xl border p-5 transition-all duration-300",
                    testResult.success
                        ? "border-green-200/60 bg-gradient-to-r from-green-50/80 to-emerald-50/40"
                        : "border-red-200/60 bg-gradient-to-r from-red-50/80 to-rose-50/40"
                )}>
                    <div className={cn(
                        "absolute top-0 left-0 right-0 h-0.5",
                        testResult.success ? "bg-gradient-to-r from-green-400 to-emerald-400" : "bg-gradient-to-r from-red-400 to-rose-400"
                    )} />
                    <div className="flex items-start gap-3">
                        {testResult.success ? (
                            <div className="p-1.5 bg-green-100 rounded-lg">
                                <CheckCircle className="h-4 w-4 text-green-600" />
                            </div>
                        ) : (
                            <div className="p-1.5 bg-red-100 rounded-lg">
                                <XCircle className="h-4 w-4 text-red-600" />
                            </div>
                        )}
                        <div className="flex-1 min-w-0">
                            <div className={cn("font-semibold text-sm", testResult.success ? 'text-green-700' : 'text-red-700')}>
                                <MessageView message={testResult.i18n || testResult.key ? testResult : asMessage(testResult.message, 'ui.connectionTestFailed')} />
                            </div>
                            {testResult.latency_ms != null && (
                                <p className="text-xs text-gray-500 mt-1 flex items-center gap-1">
                                    <Clock className="h-3 w-3" />
                                    {t('config.test.latency')} {testResult.latency_ms}ms
                                </p>
                            )}
                            {testResult.models && testResult.models.length > 0 && (
                                <div className="mt-2">
                                    <p className="text-xs text-gray-500 mb-1.5">{t('config.test.models')}</p>
                                    <div className="flex flex-wrap gap-1.5">
                                        {testResult.models.map((m) => (
                                            <span
                                                key={m}
                                                className="inline-flex items-center px-2.5 py-0.5 rounded-lg text-xs font-medium bg-white text-teal-700 border border-violet-200 shadow-sm"
                                            >
                                                <Zap className="h-3 w-3 mr-1 text-teal-400" />
                                                {m}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}

            {/* LLM 健康验证结果 */}
            {healthResult && (
                <div className={cn(
                    "relative overflow-hidden rounded-2xl border p-5 transition-all duration-300",
                    healthResult.success
                        ? "border-green-200/60 bg-gradient-to-r from-green-50/80 to-emerald-50/40"
                        : "border-red-200/60 bg-gradient-to-r from-red-50/80 to-rose-50/40"
                )}>
                    <div className={cn(
                        "absolute top-0 left-0 right-0 h-0.5",
                        healthResult.success ? "bg-gradient-to-r from-green-400 to-emerald-400" : "bg-gradient-to-r from-red-400 to-rose-400"
                    )} />
                    <div className="space-y-3">
                        <div className="flex items-center gap-2">
                            {healthResult.success ? (
                                <div className="p-1.5 bg-green-100 rounded-lg">
                                    <Heart className="h-4 w-4 text-green-600 fill-green-600" />
                                </div>
                            ) : (
                                <div className="p-1.5 bg-red-100 rounded-lg">
                                    <WifiOff className="h-4 w-4 text-red-600" />
                                </div>
                            )}
                            <h4 className="text-sm font-bold text-gray-700">{t('config.health.title')}</h4>
                        </div>
                        <div className={cn("text-sm font-semibold", healthResult.success ? 'text-green-700' : 'text-red-700')}>
                            <MessageView message={healthResult.i18n || healthResult.key ? healthResult : asMessage(healthResult.message, 'ui.healthTestFailed')} />
                        </div>
                        <div className="flex items-center gap-4 text-xs text-gray-500">
                            {healthResult.latency_ms != null && (
                                <span className="flex items-center gap-1">
                                    <Clock className="h-3 w-3" />
                                    {t('config.health.latency')} {healthResult.latency_ms}ms
                                </span>
                            )}
                            {healthResult.model_name && (
                                <span>
                                    {t('config.health.model')} <code className="px-1.5 py-0.5 rounded-md bg-white text-xs border border-gray-200 font-mono">{healthResult.model_name}</code>
                                </span>
                            )}
                        </div>
                        {healthResult.reply && (
                            <div className="mt-2 p-3 bg-white/80 rounded-xl border border-gray-200/60 shadow-sm">
                                <p className="text-[10px] uppercase tracking-wider text-gray-400 font-bold mb-1">{t('config.health.reply')}</p>
                                <p className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">{healthResult.reply}</p>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}
