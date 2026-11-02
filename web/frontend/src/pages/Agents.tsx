import { MessageView } from '@/components/MessageView'
import { asMessage, type UiMessage } from '@/i18n/messages'
import { notifyError } from '@/i18n/notify'
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { promptsService } from '../services/prompts'
import { motion } from 'framer-motion'
import { Bot, Pencil, Check, AlertCircle, Loader2, Languages } from 'lucide-react'
import { cn } from '@/utils/cn'
import {
    Dialog,
    DialogContent,
    DialogTitle,
} from "@/components/ui/dialog"
import { Button } from '@/components/ui/button'
import { MilkdownProvider } from '@milkdown/react'
import MilkdownEditorContent from '../components/MilkdownEditor'

// 智能体元数据
const AGENT_ICONS: Record<string, string> = {
    appraiser: '🎯',
    initiator: '🚀',
    orchestrator: '🎭',
    reporter: '📝',
}

// ------------------------------------------------------------------
// Milkdown 编辑器包装组件
// ------------------------------------------------------------------

const HighlightEditor = React.memo(({ content, onChange }: { content: string; onChange: (v: string) => void }) => {
    return (
        <MilkdownProvider>
            <div
                className="relative w-full h-full bg-white rounded-lg border border-gray-100/50 overflow-hidden"
                style={{ contain: 'strict' }}
            >
                <div className="prose prose-sm max-w-none h-full overflow-y-auto p-6 milkdown-theme-custom milkdown-scroll scroll-pt-2">
                    <MilkdownEditorContent content={content} onChange={onChange} />
                </div>
            </div>
        </MilkdownProvider>
    )
})

export function Agents() {
    const { t } = useTranslation()
    const [agents, setAgents] = useState<string[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<UiMessage | null>(null)

    // 弹窗状态
    const [isDialogOpen, setIsDialogOpen] = useState(false)
    const [editingAgent, setEditingAgent] = useState<string | null>(null)
    const [currentLang, setCurrentLang] = useState<'en' | 'zh'>('en')
    const [promptContent, setPromptContent] = useState('')
    const [initialContent, setInitialContent] = useState('') // 初始内容，保持不变以避免编辑器重渲染
    const [variables, setVariables] = useState<string[]>([])
    const [editorKey, setEditorKey] = useState(0) // 用于强制重新挂载编辑器
    const [isEditorLoading, setIsEditorLoading] = useState(false) // 编辑器加载状态

    // 保存状态
    const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')

    useEffect(() => {
        loadAgents()
    }, [])

    useEffect(() => {
        if (!isDialogOpen) return
        const { body } = document
        const prevOverflow = body.style.overflow
        body.style.overflow = 'hidden'
        return () => {
            body.style.overflow = prevOverflow
        }
    }, [isDialogOpen])

    // 自动隐藏保存状态
    useEffect(() => {
        if (saveStatus === 'saved') {
            const timer = setTimeout(() => setSaveStatus('idle'), 3000)
            return () => clearTimeout(timer)
        }
    }, [saveStatus])

    const loadAgents = async () => {
        try {
            const list = await promptsService.list()
            setAgents(list)
        } catch (err) {
            setError(asMessage(err, 'agents.error.loadFailed'))
            console.error(err)
        } finally {
            setLoading(false)
        }
    }

    const openEditDialog = async (agentName: string) => {
        setEditingAgent(agentName)
        setSaveStatus('idle')
        setIsDialogOpen(true)
        setIsEditorLoading(true)

        // 加载提示词
        try {
            const data = await promptsService.get(agentName, currentLang)
            setPromptContent(data.content)
            setInitialContent(data.content)
            setVariables(data.variables)
            setEditorKey(prev => prev + 1) // 强制重新挂载编辑器
        } catch (err) {
            console.error('加载提示词失败', err)
            notifyError(err)
            setPromptContent('')
            setVariables([])
        } finally {
            setIsEditorLoading(false)
        }
    }

    const switchLanguage = async (lang: 'en' | 'zh') => {
        if (!editingAgent || lang === currentLang) return

        setCurrentLang(lang)
        setSaveStatus('idle')

        try {
            const data = await promptsService.get(editingAgent, lang)
            setPromptContent(data.content)
            setInitialContent(data.content)
            setVariables(data.variables)
            setEditorKey(prev => prev + 1) // 强制重新挂载编辑器
        } catch (err) {
            console.error('切换语言失败', err)
            notifyError(err)
        }
    }

    const performSave = async () => {
        if (!editingAgent) return

        try {
            setSaveStatus('saving')
            const data = await promptsService.update(editingAgent, currentLang, promptContent)
            setVariables(data.variables)
            setSaveStatus('saved')
        } catch (err) {
            console.error('保存失败', err)
            notifyError(err)
            setSaveStatus('error')
        }
    }

    if (loading) return (
        <div className="flex h-full items-center justify-center p-8 bg-gray-50/50">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-500/80"></div>
        </div>
    )

    if (error) return (
        <div className="flex h-full items-center justify-center text-red-500 gap-2 p-8">
            <AlertCircle className="h-5 w-5" />
            <div><MessageView message={error} /></div>
        </div>
    )

    return (
        <>
            <div className="h-full w-full overflow-y-auto bg-[#f5f5f7] p-8 space-y-10">
                {/* 页面标题 */}
                <header className="mb-12 relative z-10">
                    <div className="mb-3 text-[0.72rem] font-medium uppercase tracking-[0.22em] text-black/36">
                        {t('ui.promptControl')}
                    </div>
                    <h2 className="mb-2 text-4xl font-semibold tracking-[-0.045em] text-[#1d1d1f]">
                        {t('agents.title')}
                    </h2>
                    <p className="max-w-2xl text-[1.02rem] leading-[1.55] text-black/58">
                        {t('agents.description')}
                    </p>
                </header>

                {/* 智能体卡片网格 */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                    {agents.map((agent) => {
                        const meta = {
                            name: t(`agents.meta.${agent}.name`, { defaultValue: agent }),
                            description: t(`agents.meta.${agent}.desc`, { defaultValue: t('agents.card.agent') }),
                            icon: AGENT_ICONS[agent] || '🤖',
                        }
                        return (
                            <AgentCard
                                key={agent}
                                agent={agent}
                                meta={meta}
                                onEdit={() => openEditDialog(agent)}
                            />
                        )
                    })}
                </div>

                {/* 编辑弹窗 */}
                <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                    <DialogContent className="sm:max-w-[1000px] max-h-[90vh] h-[85vh] overflow-hidden bg-white/95 border-white/20 shadow-2xl flex flex-col p-0 gap-0 overscroll-contain">

                        {/* Header */}
                        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between bg-white">
                            <div className="flex items-center gap-3">
                                <span className="text-2xl filter drop-shadow-sm">
                                    {editingAgent && (AGENT_ICONS[editingAgent] || '🤖')}
                                </span>
                                <div>
                                    <DialogTitle className="text-lg font-bold text-gray-800">
                                        {editingAgent && t(`agents.meta.${editingAgent}.name`, { defaultValue: editingAgent })}
                                    </DialogTitle>
                                    <p className="text-xs text-gray-400 font-mono">
                                        {editingAgent}.md
                                    </p>
                                </div>
                            </div>

                            <div className="flex items-center gap-4">
                                {/* 语言切换 */}
                                <div className="flex items-center gap-1 p-1 bg-gray-100/80 rounded-full border border-gray-200/50">
                                    <button
                                        onClick={() => switchLanguage('en')}
                                        className={cn(
                                            "px-3 py-1 rounded-full text-xs font-medium transition-all duration-200",
                                            currentLang === 'en'
                                                ? "bg-white shadow-sm text-amber-600"
                                                : "text-gray-500 hover:text-gray-700 hover:bg-black/5"
                                        )}
                                    >
                                        {t('agents.dialog.english')}
                                    </button>
                                    <button
                                        onClick={() => switchLanguage('zh')}
                                        className={cn(
                                            "px-3 py-1 rounded-full text-xs font-medium transition-all duration-200",
                                            currentLang === 'zh'
                                                ? "bg-white shadow-sm text-amber-600"
                                                : "text-gray-500 hover:text-gray-700 hover:bg-black/5"
                                        )}
                                    >
                                        {t('agents.dialog.chinese')}
                                    </button>
                                </div>

                                {/* 保存按钮 */}
                                <button
                                    onClick={performSave}
                                    disabled={saveStatus === 'saving'}
                                    className={cn(
                                        "px-3 py-1 rounded-full text-xs font-medium transition-all duration-200 border",
                                        saveStatus === 'saved'
                                            ? "bg-green-50 text-green-600 border-green-100"
                                            : "bg-white text-amber-600 border-amber-200/70 hover:bg-amber-50"
                                    )}
                                >
                                    <span className="inline-flex items-center gap-1.5">
                                        {saveStatus === 'saving' ? (
                                            <>
                                                <Loader2 className="w-3 h-3 animate-spin" />
                                                {t('agents.dialog.saving')}
                                            </>
                                        ) : saveStatus === 'saved' ? (
                                            <>
                                                <Check className="w-3 h-3" />
                                                {t('agents.dialog.saved')}
                                            </>
                                        ) : (
                                            <>
                                                <Pencil className="w-3 h-3" />
                                                {t('agents.dialog.save')}
                                            </>
                                        )}
                                    </span>
                                </button>
                            </div>
                        </div>

                        {/* Editor Area */}
                        <div className="flex-1 min-h-0 bg-white relative p-6" style={{ contain: 'layout style' }}>
                            <div className="h-full flex flex-col gap-2" style={{ contain: 'layout' }}>
                                <div className="flex items-center justify-between text-xs text-gray-500 px-1">
                                    <span className="flex items-center gap-1.5">
                                        <Pencil className="w-3 h-3" />
                                        {t('agents.dialog.editContent')}
                                    </span>
                                    <span className="flex items-center gap-1.5 text-amber-600/70">
                                        <Bot className="w-3 h-3" />
                                        {t('agents.dialog.variableSyntax')}
                                    </span>
                                </div>
                                <div className="flex-1 relative min-h-0">
                                    {isEditorLoading ? (
                                        <div className="h-full flex items-center justify-center">
                                            <Loader2 className="w-8 h-8 animate-spin text-amber-500/50" />
                                        </div>
                                    ) : (
                                        <HighlightEditor
                                            key={editorKey}
                                            content={initialContent}
                                            onChange={setPromptContent}
                                        />
                                    )}
                                </div>
                            </div>
                        </div>

                        {/* Footer Variables */}
                        <div className="px-6 py-3 bg-gray-50 border-t border-gray-100 flex-shrink-0">
                            <div className="flex items-center gap-3 overflow-x-auto pb-1 scrollbar-none">
                                <span className="text-[10px] uppercase tracking-wider text-gray-400 font-bold shrink-0">
                                    {t('agents.dialog.variables')}
                                </span>
                                <div className="flex items-center gap-2">
                                    {variables.map((v, i) => (
                                        <span
                                            key={i}
                                            className="inline-flex items-center px-2 py-1 rounded bg-white border border-gray-200 shadow-sm text-[11px] font-mono text-gray-600 select-all hover:border-amber-300 hover:text-amber-600 transition-colors cursor-help"
                                            title={`${t('agents.dialog.detectedVar')} {${v}}`}
                                        >
                                            <span className="opacity-50 mr-0.5">{`{`}</span>
                                            {v}
                                            <span className="opacity-50 ml-0.5">{`}`}</span>
                                        </span>
                                    ))}
                                    {variables.length === 0 && (
                                        <span className="text-xs text-gray-400 italic">{t('agents.dialog.noVariables')}</span>
                                    )}
                                </div>
                            </div>
                        </div>

                    </DialogContent>
                </Dialog>
            </div>
        </>
    )
}

// 智能体卡片组件
function AgentCard({ agent, meta, onEdit }: {
    agent: string
    meta: { name: string; description: string; icon: string }
    onEdit: () => void
}) {
    const { t } = useTranslation()

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className={cn(
                "group relative overflow-hidden rounded-2xl border transition-all duration-300",
                "bg-white shadow-sm hover:shadow-xl hover:-translate-y-1",
                "border-amber-100/50"
            )}
        >
            <div className="absolute inset-0 bg-gradient-to-br from-white/40 to-white/10 pointer-events-none" />

            {/* 顶部彩条 */}
            <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-amber-500 to-orange-500" />

            <div className="relative p-6 flex flex-col h-full">
                <div className="flex items-start gap-4 mb-4">
                    <div className="text-4xl">{meta.icon}</div>
                    <div className="flex-1">
                        <h4 className="text-lg font-bold text-gray-900 mb-1">
                            {meta.name}
                        </h4>
                        <p className="text-xs text-gray-500 font-mono">
                            {agent}
                        </p>
                    </div>
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={onEdit}
                        className="h-8 w-8 text-gray-400 hover:text-amber-600 hover:bg-amber-50"
                        title={t('agents.card.editPrompt')}
                    >
                        <Pencil className="h-4 w-4" />
                    </Button>
                </div>

                <p className="text-sm text-gray-600 flex-1 leading-relaxed">
                    {meta.description}
                </p>

                <div className="mt-4 pt-4 border-t border-gray-100/50 flex items-center justify-between text-xs text-gray-400">
                    <div className="flex items-center gap-1.5">
                        <Bot className="w-3.5 h-3.5" />
                        <span>{t('agents.card.agent')}</span>
                    </div>
                    <div className="flex items-center gap-1.5 text-amber-500">
                        <Languages className="w-3.5 h-3.5" />
                        <span>{t('agents.card.languages')}</span>
                    </div>
                </div>
            </div>
        </motion.div>
    )
}
