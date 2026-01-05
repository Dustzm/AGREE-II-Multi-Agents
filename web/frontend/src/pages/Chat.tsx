/**
 * Chat - 评价任务工作区
 * 
 * 使用时序图展示评价过程，支持文件夹层级展示
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { FileText, Loader2, RefreshCw, Sparkles, Upload, Folder, ChevronRight, ChevronDown, FolderOpen } from 'lucide-react'
import { api, type PdfFile, type FolderInfo } from '@/services/api'
import { useWebSocket, type ChatMessage } from '@/hooks/useWebSocket'
import { cn } from '@/utils/cn'
import { TimelineView, type TimelineEvent } from '@/components/TimelineView'

export function Chat() {
    const { t } = useTranslation()
    const [isLoading, setIsLoading] = useState(false)
    const [sessionId] = useState(() => `session-${Date.now()}`)
    // 层级数据
    const [folders, setFolders] = useState<FolderInfo[]>([])
    const [rootFiles, setRootFiles] = useState<PdfFile[]>([])
    const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())
    const [loadingFiles, setLoadingFiles] = useState(true)
    const [currentFile, setCurrentFile] = useState<string | null>(null)
    const [currentTaskId, setCurrentTaskId] = useState<string | null>(null)

    // 上传相关状态
    const [isUploading, setIsUploading] = useState(false)
    const [isUploadingFolder, setIsUploadingFolder] = useState(false)
    const [isDragOver, setIsDragOver] = useState(false)
    const fileInputRef = useRef<HTMLInputElement>(null)
    const folderInputRef = useRef<HTMLInputElement>(null)

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

    // Load PDF Files (层级结构)
    const loadPdfFiles = useCallback(async () => {
        setLoadingFiles(true)
        try {
            const result = await api.listPdfFiles()
            setFolders(result.folders)
            setRootFiles(result.root_files)
            // 默认全部展开
            setExpandedFolders(new Set(result.folders.map(f => f.name)))
        } catch (error) {
            console.error(t('chat.error.loadFailed'), error)
        } finally {
            setLoadingFiles(false)
        }
    }, [])

    // 切换文件夹展开状态
    const toggleFolder = useCallback((folderName: string) => {
        setExpandedFolders(prev => {
            const next = new Set(prev)
            if (next.has(folderName)) {
                next.delete(folderName)
            } else {
                next.add(folderName)
            }
            return next
        })
    }, [])

    // 处理文件上传
    const handleUpload = useCallback(async (file: File) => {
        if (!file.name.toLowerCase().endsWith('.pdf')) {
            alert(t('chat.error.pdfOnly'))
            return
        }

        setIsUploading(true)
        try {
            await api.uploadPdfToInput(file)
            await loadPdfFiles()  // 刷新文件列表
        } catch (error) {
            console.error('上传失败:', error)
            alert(error instanceof Error ? error.message : '上传失败')
        } finally {
            setIsUploading(false)
        }
    }, [loadPdfFiles])

    // 文件选择处理
    const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]
        if (file) {
            handleUpload(file)
        }
        // 重置 input 以便可以再次选择相同文件
        if (fileInputRef.current) {
            fileInputRef.current.value = ''
        }
    }, [handleUpload])

    // 处理文件夹上传
    const handleFolderSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = e.target.files
        if (!files || files.length === 0) return

        setIsUploadingFolder(true)
        try {
            const fileList = Array.from(files)
            const result = await api.uploadFolder(fileList)

            // 显示上传结果
            if (result.failed_count > 0) {
                alert(t('chat.upload.result', { success: result.success_count, failed: result.failed_count }))
            } else {
                alert(t('chat.upload.success', { count: result.success_count }))
            }

            await loadPdfFiles()  // 刷新文件列表
        } catch (error) {
            console.error('文件夹上传失败:', error)
            alert(error instanceof Error ? error.message : t('chat.error.folderUploadFailed'))
        } finally {
            setIsUploadingFolder(false)
            // 重置 input
            if (folderInputRef.current) {
                folderInputRef.current.value = ''
            }
        }
    }, [loadPdfFiles])

    // 拖拽处理
    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault()
        setIsDragOver(true)
    }, [])

    const handleDragLeave = useCallback((e: React.DragEvent) => {
        e.preventDefault()
        setIsDragOver(false)
    }, [])

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault()
        setIsDragOver(false)
        const file = e.dataTransfer.files?.[0]
        if (file) {
            handleUpload(file)
        }
    }, [handleUpload])

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
                // 检测语义证据池状态
                else if (content.includes('语义证据池')) {
                    if (content.includes('正在')) {
                        upsertEvent('preprocessing', 'preprocessing', 'running', content)
                    } else if (content.includes('完成')) {
                        upsertEvent('preprocessing', 'preprocessing', 'done', content)
                    } else if (content.includes('失败') || content.includes('异常')) {
                        upsertEvent('preprocessing', 'preprocessing', 'error', content)
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
                // 从 useTranslation 获取当前语言，转换为后端格式 (zh-CN -> zh, en-US -> en)
                const currentLang = document.documentElement.lang || 'en'
                const lang = currentLang.split('-')[0]
                send({ type: 'start', task_id: result.task_id, pdf_path: pdfFile.path, lang })
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

    // 渲染单个 PDF 文件项
    const renderPdfItem = (file: PdfFile, indent: boolean = false) => (
        <div
            key={file.path}
            onClick={() => handleSelectPdf(file)}
            className={cn(
                "group relative flex items-start p-3 rounded-lg border border-transparent transition-all duration-200 cursor-pointer",
                "hover:bg-white hover:shadow-sm hover:border-border/50",
                currentFile === file.name && "bg-primary/5 border-primary/20",
                isLoading ? "opacity-50 pointer-events-none grayscale" : "",
                indent && "ml-6"
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
    )

    return (
        <div className="flex h-full bg-gradient-to-br from-gray-50 via-gray-50 to-gray-100 isolate overflow-hidden">
            {/* Left Panel: File Explorer */}
            <div className="w-80 border-r border-gray-200/60 bg-white/95 flex flex-col transition-all relative z-20 shadow-[4px_0_24px_-12px_rgba(0,0,0,0.05)]" style={{ isolation: 'isolate' }}>
                <div className="p-6 border-b border-white/20">
                    <div className="flex items-center justify-between">
                        <div>
                            <h2 className="text-lg font-bold text-gray-800 tracking-tight">{t('chat.fileList.title')}</h2>
                            <p className="text-xs text-gray-500 mt-1">{t('chat.fileList.hint')}</p>
                        </div>
                        <div className="flex items-center gap-1">
                            <input
                                ref={fileInputRef}
                                type="file"
                                accept=".pdf"
                                onChange={handleFileSelect}
                                className="hidden"
                            />
                            <input
                                ref={folderInputRef}
                                type="file"
                                {...({ webkitdirectory: '', directory: '' } as any)}
                                multiple
                                onChange={handleFolderSelect}
                                className="hidden"
                            />
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => fileInputRef.current?.click()}
                                disabled={isUploading || isUploadingFolder}
                                className="h-9 w-9 rounded-xl transition-all duration-300 hover:bg-gradient-to-br hover:from-purple-500/20 hover:to-pink-500/20 hover:shadow-lg hover:shadow-purple-500/10 hover:scale-110 hover:backdrop-blur-sm active:scale-95 cursor-pointer"
                                title={t('chat.fileList.uploadPdf')}
                            >
                                {isUploading ? (
                                    <Loader2 className="h-4 w-4 text-gray-600 animate-spin" />
                                ) : (
                                    <Upload className="h-4 w-4 text-gray-600 transition-colors group-hover:text-purple-600" />
                                )}
                            </Button>
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => folderInputRef.current?.click()}
                                disabled={isUploading || isUploadingFolder}
                                className="h-9 w-9 rounded-xl transition-all duration-300 hover:bg-gradient-to-br hover:from-amber-500/20 hover:to-orange-500/20 hover:shadow-lg hover:shadow-amber-500/10 hover:scale-110 hover:backdrop-blur-sm active:scale-95 cursor-pointer"
                                title={t('chat.fileList.uploadFolder')}
                            >
                                {isUploadingFolder ? (
                                    <Loader2 className="h-4 w-4 text-gray-600 animate-spin" />
                                ) : (
                                    <FolderOpen className="h-4 w-4 text-gray-600 transition-colors" />
                                )}
                            </Button>
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={loadPdfFiles}
                                disabled={loadingFiles || isUploadingFolder}
                                className="h-9 w-9 rounded-xl transition-all duration-300 hover:bg-gradient-to-br hover:from-blue-500/20 hover:to-cyan-500/20 hover:shadow-lg hover:shadow-blue-500/10 hover:scale-110 hover:backdrop-blur-sm active:scale-95 cursor-pointer"
                                title={t('chat.fileList.refresh')}
                            >
                                <RefreshCw className={cn("h-4 w-4 text-gray-600 transition-colors", loadingFiles && "animate-spin")} />
                            </Button>
                        </div>
                    </div>
                </div>

                <div
                    className={cn(
                        "flex-1 overflow-y-auto p-3 space-y-2 scrollbar-thin scrollbar-thumb-muted scrollbar-track-transparent transition-colors",
                        isDragOver && "bg-primary/5"
                    )}
                    style={{ overscrollBehavior: 'contain', willChange: 'scroll-position' }}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                >
                    {loadingFiles ? (
                        <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
                            <Loader2 className="h-6 w-6 animate-spin mb-2" />
                            <span className="text-xs">{t('common.loading')}</span>
                        </div>
                    ) : (folders.length === 0 && rootFiles.length === 0) ? (
                        <div className={cn(
                            "flex flex-col items-center justify-center py-10 border-2 border-dashed rounded-xl m-2 transition-colors",
                            isDragOver ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground/50 border-muted/50"
                        )}>
                            <Upload className="h-10 w-10 mb-2 opacity-40" />
                            <p className="text-sm">{t('chat.fileList.dragHint')}</p>
                            <p className="text-xs mt-1 opacity-60">{t('chat.fileList.clickHint')}</p>
                        </div>
                    ) : (
                        <>
                            {/* 文件夹层级 */}
                            {folders.map((folder) => (
                                <div key={folder.path} className="space-y-1">
                                    {/* 文件夹标题 */}
                                    <div
                                        onClick={() => toggleFolder(folder.name)}
                                        className={cn(
                                            "flex items-center gap-2 p-2 rounded-lg cursor-pointer transition-all",
                                            "hover:bg-gray-100 text-gray-700"
                                        )}
                                    >
                                        {expandedFolders.has(folder.name) ? (
                                            <ChevronDown className="h-4 w-4 text-gray-400" />
                                        ) : (
                                            <ChevronRight className="h-4 w-4 text-gray-400" />
                                        )}
                                        <Folder className="h-4 w-4 text-amber-500" />
                                        <span className="text-sm font-medium flex-1">{folder.name}</span>
                                        <span className="text-xs text-gray-400">{folder.files.length}</span>
                                    </div>
                                    {/* 文件夹内的 PDF */}
                                    {expandedFolders.has(folder.name) && (
                                        <div className="space-y-1">
                                            {folder.files.map(file => renderPdfItem(file, true))}
                                        </div>
                                    )}
                                </div>
                            ))}
                            {/* 根目录 PDF */}
                            {rootFiles.length > 0 && folders.length > 0 && (
                                <div className="border-t border-gray-100 pt-2 mt-2">
                                    <p className="text-xs text-gray-400 px-2 mb-1">{t('chat.fileList.rootDir')}</p>
                                </div>
                            )}
                            {rootFiles.map(file => renderPdfItem(file, false))}
                        </>
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
                                {t('chat.progress.title')}
                            </h1>
                            <div className="flex items-center gap-2 mt-0.5">
                                <span className={cn(
                                    "w-1.5 h-1.5 rounded-full",
                                    isLoading ? "bg-purple-500 animate-pulse" : "bg-gray-300"
                                )} />
                                <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    {isLoading ? t('chat.progress.processing') : timelineEvents.length > 0 ? t('chat.progress.completed') : t('chat.progress.ready')}
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
