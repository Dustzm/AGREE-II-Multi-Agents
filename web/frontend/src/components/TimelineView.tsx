/**
 * TimelineView - 时序图组件
 * 
 * 展示评价过程的层级时序图，包含：
 * - PDF 解析状态（顶层）
 * - Orchestrator 和 Reporter（顶层）
 * - Initiator、Appraiser（Orchestrator 子代理，缩进显示）
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/utils/cn'
import { FileText, Bot, Sparkles, CheckCircle2, AlertCircle, Circle, Info, Database } from 'lucide-react'
import { PromptModal } from './PromptModal'

// 时序图事件类型
export interface TimelineEvent {
    id: string
    type: 'parsing' | 'preprocessing' | 'orchestrator' | 'initiator' | 'appraiser' | 'reporter' | 'error' | 'complete'
    status: 'pending' | 'running' | 'done' | 'error'
    message: string
    detail?: string  // 例如 Appraiser 的 item_id
    timestamp: Date
    prompt?: string  // Appraiser 的提示词内容
    isNew?: boolean  // 是否新添加（用于渐入动画）
    duration?: number  // 完成耗时（秒）
}

// Agent 颜色配置
const agentColors: Record<string, { pulse: string; text: string; bg: string }> = {
    parsing: { pulse: 'pulse-gray', text: 'text-gray-600', bg: 'bg-gray-100' },
    preprocessing: { pulse: 'pulse-indigo', text: 'text-indigo-600', bg: 'bg-indigo-50' },
    orchestrator: { pulse: 'pulse-green', text: 'text-green-600', bg: 'bg-green-50' },
    initiator: { pulse: 'pulse-blue', text: 'text-blue-600', bg: 'bg-blue-50' },
    appraiser: { pulse: 'pulse-purple', text: 'text-purple-600', bg: 'bg-purple-50' },
    reporter: { pulse: 'pulse-orange', text: 'text-orange-600', bg: 'bg-orange-50' },
    error: { pulse: '', text: 'text-red-600', bg: 'bg-red-50' },
    complete: { pulse: '', text: 'text-emerald-600', bg: 'bg-emerald-50' },
}

// 获取图标
function getIcon(type: TimelineEvent['type'], status: TimelineEvent['status']) {
    const iconProps = { className: 'h-4 w-4' }

    if (status === 'done') {
        return <CheckCircle2 {...iconProps} className="h-4 w-4 text-emerald-500" />
    }
    if (status === 'error') {
        return <AlertCircle {...iconProps} className="h-4 w-4 text-red-500" />
    }

    switch (type) {
        case 'parsing':
            return <FileText {...iconProps} />
        case 'preprocessing':
            return <Database {...iconProps} />
        case 'orchestrator':
            return <Sparkles {...iconProps} />
        case 'reporter':
            return <Bot {...iconProps} />
        default:
            return <Circle {...iconProps} />
    }
}

interface TimelineViewProps {
    events: TimelineEvent[]
    taskId?: string
    batchSummary?: {  // 并发评价汇总
        total: number
        completed: number
        elapsed: number
    }
}

export function TimelineView({ events, taskId, batchSummary }: TimelineViewProps) {
    const { t } = useTranslation()

    // 提示词弹窗状态
    const [promptModal, setPromptModal] = useState<{ isOpen: boolean; skillId?: string; prompt?: string }>({
        isOpen: false
    })

    // 子代理类型（需要缩进）
    const childTypes = ['initiator', 'appraiser']

    // 组织事件层级
    const organizedEvents: { event: TimelineEvent; isChild: boolean }[] = []
    let inOrchestrator = false

    for (const event of events) {
        if (event.type === 'orchestrator') {
            inOrchestrator = true
            organizedEvents.push({ event, isChild: false })
        } else if (event.type === 'reporter' || event.type === 'complete') {
            inOrchestrator = false
            organizedEvents.push({ event, isChild: false })
        } else if (childTypes.includes(event.type) && inOrchestrator) {
            organizedEvents.push({ event, isChild: true })
        } else {
            organizedEvents.push({ event, isChild: false })
        }
    }

    if (events.length === 0) {
        return (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground/50">
                <Sparkles className="h-12 w-12 mb-4 opacity-30" />
                <p className="text-sm">{t('timeline.empty.hint')}</p>
            </div>
        )
    }

    return (
        <>
            <div className="space-y-1 py-4">
                {/* 任务 ID 标题 */}
                {taskId && (
                    <div className="flex items-center gap-3 px-4 py-3 mb-4 bg-muted/30 rounded-lg border border-border/50">
                        <Sparkles className="h-5 w-5 text-primary" />
                        <div className="flex-1">
                            <p className="text-sm font-medium">{taskId}</p>
                            <p className="text-xs text-muted-foreground">{t('timeline.taskLabel')}</p>
                        </div>
                        {/* 并发进度汇总 */}
                        {batchSummary && batchSummary.total > 0 && (
                            <div className="flex items-center gap-3">
                                <div className="text-right">
                                    <p className="text-sm font-bold text-primary">
                                        {batchSummary.completed}/{batchSummary.total}
                                    </p>
                                    <p className="text-xs text-muted-foreground">
                                        {Math.floor(batchSummary.elapsed / 60)}:{String(Math.floor(batchSummary.elapsed % 60)).padStart(2, '0')}
                                    </p>
                                </div>
                                {/* 进度条 */}
                                <div className="w-24 h-2 bg-gray-200 rounded-full overflow-hidden">
                                    <div
                                        className="h-full bg-gradient-to-r from-purple-500 to-pink-500 transition-all duration-300"
                                        style={{ width: `${(batchSummary.completed / batchSummary.total) * 100}%` }}
                                    />
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {/* 时序图事件列表 */}
                <div className="space-y-0.5">
                    {organizedEvents.map(({ event, isChild }) => {
                        const colors = agentColors[event.type] || agentColors.orchestrator
                        const isRunning = event.status === 'running'
                        const hasPrompt = event.type === 'appraiser' && event.prompt

                        return (
                            <div
                                key={event.id}
                                className={cn(
                                    "timeline-item flex items-center gap-3 py-2.5 px-3 rounded-lg transition-all",
                                    isChild && "timeline-indent ml-6",
                                    event.isNew && "is-new",
                                    isRunning && "is-running"
                                )}
                            >
                                {/* 呼吸灯图标 */}
                                <div className={cn(
                                    "flex items-center justify-center w-7 h-7 rounded-full shrink-0",
                                    colors.bg,
                                    isRunning && colors.pulse
                                )}>
                                    <span className={colors.text}>
                                        {getIcon(event.type, event.status)}
                                    </span>
                                </div>

                                {/* 内容 */}
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2">
                                        <span className={cn(
                                            "text-sm font-medium",
                                            event.status === 'done' && "text-muted-foreground",
                                            event.status === 'error' && "text-red-600"
                                        )}>
                                            {event.message}
                                        </span>
                                    </div>
                                    {event.detail && (
                                        <p className="text-xs text-muted-foreground/70 mt-0.5">
                                            {event.detail}
                                        </p>
                                    )}
                                </div>

                                {/* 提示词查看按钮 - ⓘ 图标 */}
                                {hasPrompt && (
                                    <button
                                        onClick={() => setPromptModal({
                                            isOpen: true,
                                            skillId: event.detail,
                                            prompt: event.prompt
                                        })}
                                        className="flex items-center justify-center w-6 h-6 rounded-md hover:bg-muted transition-colors shrink-0 text-gray-300 hover:text-gray-500"
                                        title="查看提示词"
                                    >
                                        <Info className="h-4 w-4" />
                                    </button>
                                )}

                                {/* 时间戳/耗时 */}
                                <span className="text-[10px] text-muted-foreground/40 shrink-0">
                                    {event.duration
                                        ? `${event.duration.toFixed(1)}s`
                                        : event.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                                    }
                                </span>
                            </div>
                        )
                    })}
                </div>
            </div>

            {/* 提示词弹窗 */}
            <PromptModal
                isOpen={promptModal.isOpen}
                onClose={() => setPromptModal({ isOpen: false })}
                skillId={promptModal.skillId}
                prompt={promptModal.prompt}
            />
        </>
    )
}

