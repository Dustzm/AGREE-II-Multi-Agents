/**
 * Chat - 评价任务工作区
 * 
 * 使用时序图展示评价过程，取代传统对话框
 */
import { useState, useCallback, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { FileText, Loader2, RefreshCw, Sparkles } from 'lucide-react'
import { api, type PdfFile } from '@/services/api'
import { useWebSocket, type ChatMessage } from '@/hooks/useWebSocket'
import { cn } from '@/lib/utils'
import { TimelineView, type TimelineEvent } from '@/components/TimelineView'

export function Chat() {
    const [isLoading, setIsLoading] = useState(false)
    const [sessionId] = useState(() => `session-${Date.now()}`)
    const [pdfFiles, setPdfFiles] = useState<PdfFile[]>([])
    const [loadingFiles, setLoadingFiles] = useState(true)
    const [currentFile, setCurrentFile] = useState<string | null>(null)
    const [currentTaskId, setCurrentTaskId] = useState<string | null>(null)

    // 时序图事件
    const [timelineEvents, setTimelineEvents] = useState<TimelineEvent[]>([])

    // 添加或更新事件
    const upsertEvent = useCallback((
        id: string,
        type: TimelineEvent['type'],
        status: TimelineEvent['status'],
        message: string,
        detail?: string,
        prompt?: string
    ) => {
        setTimelineEvents(prev => {
            const existingIndex = prev.findIndex(e => e.id === id)
            const newEvent: TimelineEvent = {
                id,
                type,
                status,
                message,
                detail,
                prompt,
                timestamp: new Date()
            }

            if (existingIndex >= 0) {
                // 更新现有事件
                const updated = [...prev]
                updated[existingIndex] = { ...updated[existingIndex], ...newEvent }
                return updated
            } else {
                // 添加新事件
                return [...prev, newEvent]
            }
        })
    }, [])

    // Load PDF Files
    const loadPdfFiles = useCallback(async () => {
        setLoadingFiles(true)
        try {
            const result = await api.listPdfFiles()
            setPdfFiles(result.files)
        } catch (error) {
            console.error('加载文件列表失败:', error)
        } finally {
            setLoadingFiles(false)
        }
    }, [])

    useEffect(() => {
        loadPdfFiles()
    }, [loadPdfFiles])

    const { connect, send } = useWebSocket({
        onMessage: (msg: ChatMessage) => {
            // 解析 agent 名称获取事件类型
            const agentToType = (agent: string | undefined): TimelineEvent['type'] => {
                if (!agent) return 'orchestrator'
                const lower = agent.toLowerCase()
                if (lower.includes('initiator')) return 'initiator'
                if (lower.includes('appraiser')) return 'appraiser'
                if (lower.includes('reporter')) return 'reporter'
                if (lower.includes('orchestrator')) return 'orchestrator'
                return 'orchestrator'
            }

            if (msg.type === 'agent') {
                const eventType = agentToType(msg.agent)
                const content = msg.content || `${msg.agent} 正在处理...`

                // 解析 Appraiser 的 item_id
                let eventId: string = eventType
                let detail: string | undefined

                if (eventType === 'appraiser' && content.includes('item_')) {
                    // 提取 item_X
                    const match = content.match(/item_\d+/)
                    if (match) {
                        eventId = `appraiser_${match[0]}`
                        detail = match[0]
                    }
                }

                // 在添加新事件前，将之前运行中的事件标记为完成
                setTimelineEvents(prev => {
                    const updated = prev.map(e => {
                        // 如果是不同类型的代理切换，标记前一个为完成
                        if (e.status === 'running' && e.type !== eventType) {
                            let doneMessage = e.message
                            if (e.message.includes('正在')) {
                                doneMessage = e.message.replace('正在', '').replace('...', ' 已完成')
                            } else if (e.message.endsWith('...')) {
                                doneMessage = e.message.replace('...', ' 已完成')
                            }
                            return { ...e, status: 'done' as const, message: doneMessage }
                        }
                        // 如果是同类型（Appraiser）但不同 item，标记前一个 item 为完成
                        if (e.status === 'running' && e.type === 'appraiser' && eventType === 'appraiser' && e.id !== eventId) {
                            let doneMessage = e.message
                            if (e.message.includes('正在')) {
                                doneMessage = e.message.replace('正在', '').replace('...', ' 已完成')
                            } else if (e.message.endsWith('...')) {
                                doneMessage = e.message.replace('...', ' 已完成')
                            }
                            return { ...e, status: 'done' as const, message: doneMessage }
                        }
                        return e
                    })
                    return updated
                })

                upsertEvent(eventId, eventType, 'running', content, detail, msg.prompt)

            } else if (msg.type === 'status') {
                const content = msg.content || ''

                // 检测 PDF 解析状态
                if (content.includes('解析') || content.includes('PDF')) {
                    if (content.includes('完成')) {
                        upsertEvent('parsing', 'parsing', 'done', content)
                    } else {
                        upsertEvent('parsing', 'parsing', 'running', content)
                    }
                }
                // 检测 Orchestrator 启动
                else if (content.includes('智能体') || content.includes('启动')) {
                    upsertEvent('orchestrator', 'orchestrator', 'running', '协调评价任务...')
                }
                // 检测任务 ID
                else if (content.includes('任务')) {
                    // 静默处理，不添加事件
                }

            } else if (msg.type === 'complete') {
                setIsLoading(false)
                // 标记所有运行中的事件为完成，并修改消息文字
                setTimelineEvents(prev => prev.map(e => {
                    if (e.status === 'running') {
                        // 将"正在xxx"改为"xxx已完成"
                        let doneMessage = e.message
                        if (e.message.includes('正在')) {
                            doneMessage = e.message.replace('正在', '').replace('...', ' 已完成')
                        } else if (e.message.endsWith('...')) {
                            doneMessage = e.message.replace('...', ' 已完成')
                        }
                        return { ...e, status: 'done' as const, message: doneMessage }
                    }
                    return e
                }))
                upsertEvent('complete', 'complete', 'done', '评价完成')

            } else if (msg.type === 'error') {
                setIsLoading(false)
                upsertEvent('error', 'error', 'error', msg.content || '发生错误')
            }
        },
        onError: () => {
            setIsLoading(false)
            upsertEvent('error', 'error', 'error', '连接断开')
        },
    })

    const handleSelectPdf = async (pdfFile: PdfFile) => {
        if (isLoading) return

        // 重置时序图
        setTimelineEvents([])
        setCurrentFile(pdfFile.name)
        setIsLoading(true)

        // 添加初始解析事件
        upsertEvent('parsing', 'parsing', 'running', '正在解析 PDF...')

        try {
            const result = await api.startTask(pdfFile.path)
            setCurrentTaskId(result.task_id)  // 保存 task_id

            connect(sessionId)
            setTimeout(() => {
                send({ type: 'start', task_id: result.task_id, pdf_path: pdfFile.path })
            }, 500)

        } catch (error) {
            upsertEvent('error', 'error', 'error', `启动失败: ${error}`)
            setIsLoading(false)
        }
    }

    const formatFileSize = (bytes: number) => {
        if (bytes < 1024) return `${bytes} B`
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    }

    return (
        <div className="flex h-full bg-gradient-to-br from-gray-50 via-gray-50 to-gray-100 isolate overflow-hidden">
            {/* Left Panel: File Explorer */}
            <div className="w-80 border-r border-gray-200/60 bg-white/95 flex flex-col transition-all relative z-20 shadow-[4px_0_24px_-12px_rgba(0,0,0,0.05)]" style={{ isolation: 'isolate' }}>
                <div className="p-6 border-b border-white/20">
                    <div className="flex items-center justify-between">
                        <div>
                            <h2 className="text-lg font-bold text-gray-800 tracking-tight">文档列表</h2>
                            <p className="text-xs text-gray-500 mt-1">Select PDF to start</p>
                        </div>
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={loadPdfFiles}
                            disabled={loadingFiles}
                            className="h-9 w-9 hover:bg-white/60 rounded-xl"
                        >
                            <RefreshCw className={cn("h-4 w-4 text-gray-600", loadingFiles && "animate-spin")} />
                        </Button>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto p-3 space-y-2 scrollbar-thin scrollbar-thumb-muted scrollbar-track-transparent" style={{ overscrollBehavior: 'contain', willChange: 'scroll-position' }}>
                    {loadingFiles ? (
                        <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
                            <Loader2 className="h-6 w-6 animate-spin mb-2" />
                            <span className="text-xs">加载中...</span>
                        </div>
                    ) : pdfFiles.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-10 text-muted-foreground/50 border-2 border-dashed border-muted/50 rounded-xl m-2">
                            <FileText className="h-10 w-10 mb-2 opacity-20" />
                            <p className="text-sm">暂无 PDF 文件</p>
                        </div>
                    ) : (
                        pdfFiles.map((file) => (
                            <div
                                key={file.path}
                                onClick={() => handleSelectPdf(file)}
                                className={cn(
                                    "group relative flex items-start p-3 rounded-lg border border-transparent transition-all duration-200 cursor-pointer",
                                    "hover:bg-white hover:shadow-sm hover:border-border/50",
                                    currentFile === file.name && "bg-primary/5 border-primary/20",
                                    isLoading ? "opacity-50 pointer-events-none grayscale" : ""
                                )}
                            >
                                <div className={cn(
                                    "p-2 rounded-md mr-3 shrink-0 transition-colors",
                                    currentFile === file.name
                                        ? "bg-primary/10 text-primary"
                                        : "bg-red-50 text-red-500 group-hover:bg-red-100"
                                )}>
                                    <FileText className="h-4 w-4" />
                                </div>
                                <div className="min-w-0 flex-1">
                                    <h3 className={cn(
                                        "text-sm font-medium truncate transition-colors",
                                        currentFile === file.name
                                            ? "text-primary"
                                            : "text-foreground/90 group-hover:text-primary"
                                    )}>
                                        {file.name}
                                    </h3>
                                    <p className="text-xs text-muted-foreground/70 mt-1 flex items-center gap-2">
                                        <span>{formatFileSize(file.size)}</span>
                                        <span className="w-0.5 h-0.5 rounded-full bg-muted-foreground/50" />
                                        <span>{file.modified}</span>
                                    </p>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>

            {/* Right Panel: Timeline View */}
            <div className="flex-1 flex flex-col relative z-10">
                {/* Header */}
                <div className="h-20 border-b border-gray-200/50 flex items-center justify-between px-8 bg-white/95 sticky top-0 z-10">
                    <div className="absolute top-0 left-0 w-full h-full bg-gradient-to-r from-purple-500/5 to-pink-500/5 pointer-events-none" />

                    <div className="flex items-center gap-4 relative">
                        <div className="p-2.5 bg-gradient-to-br from-purple-500 to-pink-500 rounded-xl shadow-lg shadow-purple-500/20 text-white">
                            <Sparkles className="h-5 w-5" />
                        </div>
                        <div className="flex flex-col">
                            <h1 className="text-xl font-bold text-gray-900 tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-purple-600 to-pink-600">
                                评价进度
                            </h1>
                            <div className="flex items-center gap-2 mt-0.5">
                                <span className={cn(
                                    "w-1.5 h-1.5 rounded-full",
                                    isLoading ? "bg-purple-500 animate-pulse" : "bg-gray-300"
                                )} />
                                <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    {isLoading ? 'Processing' : timelineEvents.length > 0 ? 'Completed' : 'Ready'}
                                </p>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Timeline Area */}
                <div className="flex-1 overflow-y-auto px-6 scrollbar-thin scrollbar-thumb-muted-foreground/10 hover:scrollbar-thumb-muted-foreground/20" style={{ overscrollBehavior: 'contain', willChange: 'scroll-position' }}>
                    <TimelineView
                        events={timelineEvents}
                        taskId={currentTaskId || undefined}
                    />
                </div>
            </div>
        </div>
    )
}
