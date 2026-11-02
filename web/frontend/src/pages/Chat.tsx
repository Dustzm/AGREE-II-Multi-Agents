import { formatDate } from '@/i18n/format'
import { asMessage, uploadMessage, type UiMessage } from '@/i18n/messages'
import { taskEvent } from '@/i18n/taskMessages'
import { notifyError, notifySuccess } from '@/i18n/notify'
/**
 * Chat - 评价任务工作区
 * 
 * 使用时序图展示评价过程，支持文件夹层级展示
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { FileText, Loader2, RefreshCw, Upload, Folder, ChevronRight, ChevronDown, FolderOpen, Paperclip, Eye, EyeOff, FileSpreadsheet, FileImage, Film, Music, FileCode, File, Presentation } from 'lucide-react'
import { api, type PdfFile, type InputDirectoryNode } from '@/services/api'
import { useWebSocket, type ChatMessage } from '@/hooks/useWebSocket'
import { cn } from '@/utils/cn'
import { TimelineView, type TimelineEvent } from '@/components/TimelineView'

function getFileStem(filename: string) {
    return filename.replace(/\.[^.]+$/, '')
}

function isSiblingAttachment(mainName: string, suspectName: string) {
    const mainStem = getFileStem(mainName)
    const suspectStem = getFileStem(suspectName)

    if (!suspectStem.startsWith(mainStem)) return false
    if (suspectStem.length <= mainStem.length) return false

    const nextChar = suspectStem[mainStem.length]
    return nextChar === ' ' || nextChar === '_' || nextChar === '-' || /[\u4e00-\u9fff]/.test(nextChar)
}

function collectDirectoryPaths(node: InputDirectoryNode): string[] {
    return node.children.flatMap((child) => [child.path, ...collectDirectoryPaths(child)])
}

function collectFiles(node: InputDirectoryNode): PdfFile[] {
    return [...node.files, ...node.children.flatMap(collectFiles)]
}

function getParentDirectory(filePath: string) {
    const parts = filePath.split('/')
    parts.pop()
    return parts.join('/')
}

type TaskPhase = 'idle' | 'ready' | 'starting' | 'running' | 'stopping' | 'completed' | 'partial' | 'cancelled' | 'error'
type PersistedTimelineEvent = Omit<TimelineEvent, 'timestamp'> & { timestamp: string }

function isBusyPhase(phase: TaskPhase) {
    return phase === 'starting' || phase === 'running' || phase === 'stopping'
}

const folderInputAttributes = {
    webkitdirectory: '',
    directory: '',
} as React.InputHTMLAttributes<HTMLInputElement> & {
    webkitdirectory: string
    directory: string
}

export function Chat() {
    const { t, i18n } = useTranslation()
    // 持久化状态初始化
    const [sessionId] = useState(() => {
        // 任务状态保存在 localStorage，会话 ID 也须跨标签页和浏览器重启保持一致，才能收到后端积压的终态消息
        const saved = localStorage.getItem('ama_session_id') || sessionStorage.getItem('ama_session_id')
        const id = saved || `session-${Date.now()}`
        localStorage.setItem('ama_session_id', id)
        return id
    })

    // 从 localStorage 恢复初始状态
    const [taskPhase, setTaskPhase] = useState<TaskPhase>(() => {
        const saved = localStorage.getItem('ama_task_phase') as TaskPhase | null
        if (saved) return saved
        return localStorage.getItem('ama_is_loading') === 'true' ? 'running' : 'idle'
    })

    const [currentFile, setCurrentFile] = useState<string | null>(() => {
        return localStorage.getItem('ama_current_file')
    })

    const [currentTaskId, setCurrentTaskId] = useState<string | null>(() => {
        return localStorage.getItem('ama_task_id')
    })

    const [timelineEvents, setTimelineEvents] = useState<TimelineEvent[]>(() => {
        try {
            const saved = localStorage.getItem('ama_timeline_events')
            if (!saved) return []
            const events = JSON.parse(saved) as PersistedTimelineEvent[]
            // JSON.parse 会将 Date 对象转为字符串，需要手动通过 new Date() 恢复
            return events
                .filter((e) => e.id !== 'connection_error') // 确保加载时不包含连接错误
                .map((e) => ({
                    ...e,
                    ui_message: e.ui_message || (e.type === 'appraiser' && e.detail ? { ...asMessage(e.message), params: { ...asMessage(e.message).params, skill_id: e.detail } } : asMessage(e.message)),
                    timestamp: new Date(e.timestamp)
                }))
        } catch {
            return []
        }
    })

    const [batchSummary, setBatchSummary] = useState<{ total: number; completed: number; elapsed: number }>(() => {
        try {
            const saved = localStorage.getItem('ama_batch_summary')
            return saved ? JSON.parse(saved) : { total: 0, completed: 0, elapsed: 0 }
        } catch {
            return { total: 0, completed: 0, elapsed: 0 }
        }
    })
    const [connectionError, setConnectionError] = useState(false)

    // 层级数据
    const [fileTree, setFileTree] = useState<InputDirectoryNode | null>(null)
    const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())
    const [loadingFiles, setLoadingFiles] = useState(true)
    const [hiddenAttachments, setHiddenAttachments] = useState<Set<string>>(new Set())  // 存储隐藏附件的主 PDF 路径

    // 上传相关状态
    const [isUploading, setIsUploading] = useState(false)
    const [isUploadingFolder, setIsUploadingFolder] = useState(false)
    const [isDragOver, setIsDragOver] = useState(false)
    const fileInputRef = useRef<HTMLInputElement>(null)
    const folderInputRef = useRef<HTMLInputElement>(null)

    // 状态更新时的副作用：同步到 localStorage
    useEffect(() => {
        localStorage.setItem('ama_task_phase', taskPhase)
        localStorage.setItem('ama_is_loading', String(isBusyPhase(taskPhase)))
    }, [taskPhase])

    useEffect(() => {
        if (currentFile) localStorage.setItem('ama_current_file', currentFile)
        else localStorage.removeItem('ama_current_file')
    }, [currentFile])

    useEffect(() => {
        if (currentTaskId) localStorage.setItem('ama_task_id', currentTaskId)
        else localStorage.removeItem('ama_task_id')
    }, [currentTaskId])

    useEffect(() => {
        // 过滤掉连接错误的事件，避免刷新后看到"Connection lost"
        const persistedEvents = timelineEvents.filter(e => e.id !== 'connection_error')
        localStorage.setItem('ama_timeline_events', JSON.stringify(persistedEvents))
    }, [timelineEvents])

    useEffect(() => {
        localStorage.setItem('ama_batch_summary', JSON.stringify(batchSummary))
    }, [batchSummary])

    // 清理任务状态（保留 Session）
    const clearTaskState = useCallback((nextPhase: TaskPhase = 'idle') => {
        setTimelineEvents([])
        setBatchSummary({ total: 0, completed: 0, elapsed: 0 })
        setCurrentTaskId(null)
        setTaskPhase(nextPhase)
        setConnectionError(false)
        // localStorage 会通过 useEffect 自动更新
    }, [])

    // 检查服务是否重启
    useEffect(() => {
        const checkSystemStatus = async () => {
            try {
                const health = await api.getHealth()
                const serverStartup = String(health.startup_time)
                const localStartup = localStorage.getItem('ama_startup_time')

                // 服务重启后保留任务 ID 和 PDF 路径，供人工续跑。
                if (localStartup && localStartup !== serverStartup) {
                    localStorage.setItem('ama_startup_time', serverStartup)
                    setTaskPhase(prev => isBusyPhase(prev) ? 'error' : prev)
                } else if (!localStartup) {
                    // 首次运行，记录启动时间
                    localStorage.setItem('ama_startup_time', serverStartup)
                }
            } catch (error) {
                console.error('检查系统状态失败:', error)
            }
        }

        checkSystemStatus()
    }, [])

    // 添加或更新事件
    const upsertEvent = useCallback((
        id: string,
        type: TimelineEvent['type'],
        status: TimelineEvent['status'],
        message: string | UiMessage,
        detail?: string,
        prompt?: string
    ) => {
        setTimelineEvents(prev => {
            const existingIndex = prev.findIndex(e => e.id === id)
            const newEvent: TimelineEvent = {
                id,
                type,
                status,
                message: typeof message === 'string' ? message : '',
                ui_message: asMessage(message, status === 'error' ? 'chat.error.occurred' : 'chat.status.processing'),
                detail,
                prompt,
                timestamp: new Date()
            }

            if (existingIndex >= 0) {
                // 更新现有事件
                const updated = [...prev]
                updated[existingIndex] = { ...updated[existingIndex], ...newEvent, isNew: false }
                return updated
            } else {
                // 添加新事件，标记为新
                return [...prev, { ...newEvent, isNew: true }]
            }
        })
    }, [])

    // Load PDF Files (层级结构)
    const loadPdfFiles = useCallback(async () => {
        setLoadingFiles(true)
        try {
            const result = await api.listPdfFiles()
            setFileTree(result.tree)
            // 默认全部展开
            setExpandedFolders(new Set(collectDirectoryPaths(result.tree)))
        } catch (error) {
            console.error(t('chat.error.loadFailed'), error)
        } finally {
            setLoadingFiles(false)
        }
    }, [t])

    // 切换文件夹展开状态
    const toggleFolder = useCallback((folderPath: string) => {
        setExpandedFolders(prev => {
            const next = new Set(prev)
            if (next.has(folderPath)) {
                next.delete(folderPath)
            } else {
                next.add(folderPath)
            }
            return next
        })
    }, [])

    // 处理文件上传
    const handleUpload = useCallback(async (file: File) => {
        if (!file.name.toLowerCase().endsWith('.pdf')) {
            notifyError({ key: 'chat.error.pdfOnly' })
            return
        }

        setIsUploading(true)
        try {
            await api.uploadPdfToInput(file)
            await loadPdfFiles()  // 刷新文件列表
        } catch (error) {
            console.error('Upload failed:', error)
            notifyError(error, 'chat.error.uploadFailed')
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
                notifyError(uploadMessage('chat.upload.result', result))
            } else {
                notifySuccess('chat.upload.success', { count: result.success_count })
            }

            await loadPdfFiles()  // 刷新文件列表
        } catch (error) {
            console.error('Folder upload failed:', error)
            notifyError(error, 'chat.error.folderUploadFailed')
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

    const isBusy = isBusyPhase(taskPhase)

    const markCancelled = useCallback(() => {
        setTaskPhase('cancelled')
        setTimelineEvents(prev => prev.map(e => {
            if (e.status === 'running') {
                const doneMessage = e.message.endsWith('...')
                    ? e.message.replace(/\.\.\.$/, '')
                    : e.message
                return { ...e, status: 'done' as const, message: doneMessage }
            }
            return e
        }))
        upsertEvent('cancelled', 'cancelled', 'done', { key: 'chat.status.cancelled' })
    }, [upsertEvent])

    // 连接建立时以后端快照为准：终态消息丢失后不能永久停在执行中或终止中
    const reconcileSessionState = (snapshot: ChatMessage) => {
        const serverActive = snapshot.state !== 'idle' && snapshot.task_id === currentTaskId
        if (serverActive) {
            if (snapshot.state === 'stopping') setTaskPhase('stopping')
            // 停止请求未送达后端（例如断线期间刷新页面），恢复为可再次终止
            else if (taskPhase === 'stopping') setTaskPhase('running')
        } else if (taskPhase === 'stopping') {
            markCancelled()
        } else if (taskPhase === 'running') {
            // starting 不校正：启动指令可能尚未到达后端
            setTaskPhase('error')
            upsertEvent('error', 'error', 'error', { key: 'chat.error.taskLost' })
        }
    }

    const { connect, send, reconnectAttempts } = useWebSocket({
        onOpen: () => {
            setConnectionError(false)
        },
        onMessage: (msg: ChatMessage) => {
            if (msg.type === 'session_state') {
                reconcileSessionState(msg)
                return
            }

            if (msg.task_id) {
                setCurrentTaskId(msg.task_id)
            }

            if (msg.type === 'status' || msg.type === 'agent' || msg.type === 'item_progress' || msg.type === 'batch_summary') {
                setTaskPhase(prev => ['starting', 'idle', 'ready'].includes(prev) ? 'running' : prev)
            }

            if (msg.type === 'agent' || msg.type === 'status') {
                const event = taskEvent(msg)
                if (event) {
                    setTimelineEvents(prev => prev.map(entry => entry.status === 'running' && entry.type !== event.type
                        ? { ...entry, status: 'done' as const } : entry))
                    upsertEvent(event.id, event.type, event.status, event.message, msg.skill_id, msg.prompt)
                }
                if (msg.state === 'stopping') setTaskPhase('stopping')
            } else if (msg.type === 'complete') {
                setTaskPhase('completed')
                // 标记所有运行中的事件为完成
                setTimelineEvents(prev => prev.map(e => {
                    if (e.status === 'running') {
                        // 移除消息末尾的 "..."
                        let doneMessage = e.message
                        if (e.message.endsWith('...')) {
                            doneMessage = e.message.replace(/\.\.\.$/, '')
                        }
                        return { ...e, status: 'done' as const, message: doneMessage }
                    }
                    return e
                }))
                upsertEvent('complete', 'complete', 'done', { key: 'chat.status.complete' })

            } else if (msg.type === 'partial') {
                setTaskPhase('partial')
                upsertEvent('partial', 'reporter', 'done', { key: 'chat.status.partial', params: { completed: 23 - (msg.missing_items?.length || 0) } })
                Object.entries(msg.failed_details || {}).forEach(([item, failure]) => {
                    upsertEvent(`appraiser_${item}`, 'appraiser', 'error', { ...asMessage(failure.i18n ? failure : failure.message, 'server.error.appraisal_failed'), diagnostic: failure.diagnostic || asMessage(failure.message).diagnostic || `${item} · ${failure.type} (${failure.stage})` })
                })

            } else if (msg.type === 'cancelled') {
                markCancelled()

            } else if (msg.type === 'error') {
                if (msg.i18n?.key === 'error.task_already_running') {
                    notifyError(msg)
                    return
                }
                setTaskPhase('error')
                upsertEvent('error', 'error', 'error', msg.i18n ? msg : asMessage(msg.content, 'chat.error.occurred'))
                Object.entries(msg.failed_details || {}).forEach(([item, failure]) => {
                    upsertEvent(`appraiser_${item}`, 'appraiser', 'error', { ...asMessage(failure.i18n ? failure : failure.message, 'server.error.appraisal_failed'), diagnostic: failure.diagnostic || asMessage(failure.message).diagnostic || `${item} · ${failure.type} (${failure.stage})` })
                })

            } else if (msg.type === 'item_progress') {
                const { skill_id, item_status, duration } = msg
                if (!skill_id) return
                const status = item_status === 'completed' ? 'done' : item_status === 'failed' ? 'error' : 'running'
                const key = item_status === 'completed' ? 'timeline.evaluated' : item_status === 'failed' ? 'timeline.evaluateFailed' : 'timeline.evaluating'
                upsertEvent(`appraiser_${skill_id}`, 'appraiser', status,
                    { key, params: { ...msg.i18n?.params, skill_id } }, skill_id)
                if (duration !== undefined) setTimelineEvents(prev => prev.map(event => event.id === `appraiser_${skill_id}` ? { ...event, duration } : event))
            } else if (msg.type === 'batch_summary') {
                // 并发评价汇总进度
                setBatchSummary({
                    total: msg.total || 0,
                    completed: msg.completed_count || 0,
                    elapsed: msg.elapsed || 0
                })
            }
        },
        onError: () => {
            // 连接错误不写入时序图，而是通过 UI 状态提示
            setConnectionError(true)
        },
    })

    // 初始化时，如果检测到正在运行的任务，自动重连
    useEffect(() => {
        if (isBusy && sessionId) {
            console.log('检测到中断的任务，尝试重新连接:', sessionId)
            connect(sessionId)
        }
    }, [connect, isBusy, sessionId])

    const handleSelectPdf = (pdfFile: PdfFile) => {
        if (isBusy) return

        setCurrentFile(pdfFile.path)
        clearTaskState('ready')
    }

    const handleStartTask = useCallback(async () => {
        if (!currentFile || isBusy) return

        clearTaskState('starting')

        // 添加初始解析事件
        upsertEvent('parsing', 'parsing', 'running', { key: 'chat.status.parsing' })

        try {
            const result = await api.startTask(currentFile)
            setCurrentTaskId(result.task_id)

            // 连接 WebSocket
            connect(sessionId)

            // 延迟发送 start 指令，确保连接建立
            setTimeout(() => {
                const currentLang = document.documentElement.lang || 'en'
                const lang = currentLang.split('-')[0]
                send({ type: 'start', task_id: result.task_id, pdf_path: currentFile, lang })
            }, 500)
        } catch (error) {
            upsertEvent('error', 'error', 'error', asMessage(error, 'chat.error.occurred'))
            setTaskPhase('error')
        }
    }, [clearTaskState, connect, currentFile, isBusy, send, sessionId, upsertEvent])

    const handleResumeTask = useCallback(() => {
        if (!currentFile || !currentTaskId || isBusy) return
        setTaskPhase('starting')
        setConnectionError(false)
        connect(sessionId)
        setTimeout(() => {
            const lang = (document.documentElement.lang || 'en').split('-')[0]
            send({ type: 'resume', task_id: currentTaskId, pdf_path: currentFile, lang })
        }, 500)
    }, [connect, currentFile, currentTaskId, isBusy, send, sessionId])

    const handleStopTask = useCallback(() => {
        if (!currentTaskId || (taskPhase !== 'starting' && taskPhase !== 'running')) return

        setTaskPhase('stopping')
        setConnectionError(false)

        const currentLang = document.documentElement.lang || 'en'
        const lang = currentLang.split('-')[0]
        send({ type: 'cancel', task_id: currentTaskId, lang })
    }, [currentTaskId, send, taskPhase])

    const selectedFileName = currentFile?.split('/').pop() ?? null
    const canStart = Boolean(currentFile) && ['ready', 'completed', 'partial', 'cancelled', 'error'].includes(taskPhase)
    const canResume = Boolean(currentFile && currentTaskId) && ['partial', 'cancelled', 'error'].includes(taskPhase)
    const canStop = taskPhase === 'starting' || taskPhase === 'running'

    const phaseLabel = (() => {
        if (connectionError) return t('chat.error.disconnected')
        switch (taskPhase) {
            case 'idle':
                return t('chat.progress.idle')
            case 'ready':
                return t('chat.progress.readyToStart')
            case 'starting':
                return t('chat.progress.starting')
            case 'running':
                return t('chat.progress.processing')
            case 'stopping':
                return t('chat.progress.stopping')
            case 'completed':
                return t('chat.progress.completed')
            case 'partial':
                return t('chat.progress.partial')
            case 'cancelled':
                return t('chat.progress.cancelled')
            case 'error':
                return t('chat.progress.error')
            default:
                return t('chat.progress.ready')
        }
    })()

    const formatFileSize = (bytes: number) => {
        if (bytes < 1024) return `${bytes.toLocaleString(i18n.language)} B`
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toLocaleString(i18n.language, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} KB`
        return `${(bytes / (1024 * 1024)).toLocaleString(i18n.language, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`
    }

    const getFileTypeStyle = (fileName: string) => {
        const ext = fileName.split('.').pop()?.toLowerCase() || ''

        if (['pdf'].includes(ext)) {
            return { icon: FileText, bgColor: 'bg-red-50', textColor: 'text-red-500' }
        }
        if (['docx', 'doc', 'rtf', 'txt', 'md'].includes(ext)) {
            return { icon: FileText, bgColor: 'bg-blue-50', textColor: 'text-blue-500' }
        }
        if (['xlsx', 'xls'].includes(ext)) {
            return { icon: FileSpreadsheet, bgColor: 'bg-green-50', textColor: 'text-green-600' }
        }
        if (['pptx', 'ppt'].includes(ext)) {
            return { icon: Presentation, bgColor: 'bg-orange-50', textColor: 'text-orange-500' }
        }
        if (['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'ico', 'tiff', 'tif'].includes(ext)) {
            return { icon: FileImage, bgColor: 'bg-purple-50', textColor: 'text-purple-500' }
        }
        if (['mp4', 'mov', 'avi', 'mkv', 'webm'].includes(ext)) {
            return { icon: Film, bgColor: 'bg-pink-50', textColor: 'text-pink-500' }
        }
        if (['mp3', 'wav', 'flac', 'aac', 'ogg'].includes(ext)) {
            return { icon: Music, bgColor: 'bg-cyan-50', textColor: 'text-cyan-500' }
        }
        if (['json', 'xml', 'html', 'css', 'js', 'ts', 'py'].includes(ext)) {
            return { icon: FileCode, bgColor: 'bg-slate-50', textColor: 'text-slate-500' }
        }

        return { icon: File, bgColor: 'bg-gray-50', textColor: 'text-gray-500' }
    }

    // 渲染单个 PDF 文件项（支持附件数量显示和独立隐藏）
    const renderPdfItem = (file: PdfFile, depth: number, attachmentCount: number = 0, mainFilePath?: string) => {
        const isAttachment = file.is_attachment === true
        const offset = depth * 14
        const isActive = currentFile === file.path

        if (isAttachment) {
            if (mainFilePath && hiddenAttachments.has(mainFilePath)) return null

            const { icon: IconComponent, bgColor, textColor } = getFileTypeStyle(file.name)

            return (
                <div
                    key={file.path}
                    style={{ marginLeft: `${offset + 18}px` }}
                    className={cn(
                        'group relative flex items-start p-2 rounded-lg border border-transparent transition-all cursor-default'
                    )}
                >
                    <div className={cn("p-1.5 rounded-md mr-2 shrink-0", bgColor, textColor)}>
                        <IconComponent className="h-3.5 w-3.5" />
                    </div>
                    <div className="min-w-0 flex-1">
                        <h3 className="text-xs font-normal text-foreground/80 truncate">
                            {file.name}
                        </h3>
                        <p className="text-xs text-muted-foreground/70 mt-0.5 flex items-center gap-1.5">
                            <span>{formatFileSize(file.size)}</span>
                            <span className="w-0.5 h-0.5 rounded-full bg-muted-foreground/50" />
                            <span>{formatDate(file.modified, i18n.language)}</span>
                        </p>
                    </div>
                </div>
            )
        }

        // 普通 PDF 文件
        return (
            <div
                key={file.path}
                onClick={() => handleSelectPdf(file)}
                style={{ marginLeft: `${offset}px` }}
                className={cn(
                    "group relative flex items-start p-3 rounded-lg border border-transparent transition-all duration-200 cursor-pointer",
                    "hover:bg-white hover:shadow-sm hover:border-border/50",
                    isActive && "bg-primary/5 border-primary/20",
                    isBusy ? "opacity-50 pointer-events-none grayscale" : ""
                )}
            >
                <div className={cn(
                    "p-2 rounded-md mr-3 shrink-0 transition-colors",
                    isActive
                        ? "bg-primary/10 text-primary"
                        : "bg-red-50 text-red-500 group-hover:bg-red-100"
                )}>
                    <FileText className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                    <h3 className={cn(
                        "text-sm font-medium truncate transition-colors flex items-center gap-2",
                        isActive
                            ? "text-primary"
                            : "text-foreground/90 group-hover:text-primary"
                    )}>
                        {file.name}
                        {/* 附件数量徽标（可点击切换显示/隐藏） */}
                        {attachmentCount > 0 && (
                            <button
                                onClick={(e) => {
                                    e.stopPropagation()  // 阻止触发 PDF 选择
                                    setHiddenAttachments(prev => {
                                        const next = new Set(prev)
                                        if (next.has(file.path)) {
                                            next.delete(file.path)
                                        } else {
                                            next.add(file.path)
                                        }
                                        return next
                                    })
                                }}
                                className={cn(
                                    "inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[10px] transition-colors cursor-pointer hover:scale-105",
                                    hiddenAttachments.has(file.path)
                                        ? "bg-gray-100 text-gray-400"
                                        : "bg-amber-100 text-amber-600 hover:bg-amber-200"
                                )}
                                title={hiddenAttachments.has(file.path) ? t('chat.fileList.showAttachments') : t('chat.fileList.hideAttachments')}
                            >
                                <Paperclip className="h-2.5 w-2.5" />
                                {attachmentCount}
                                {hiddenAttachments.has(file.path) ? (
                                    <EyeOff className="h-2.5 w-2.5 ml-0.5" />
                                ) : (
                                    <Eye className="h-2.5 w-2.5 ml-0.5" />
                                )}
                            </button>
                        )}
                    </h3>
                    <p className="text-xs text-muted-foreground/70 mt-1 flex items-center gap-2">
                        <span>{formatFileSize(file.size)}</span>
                        <span className="w-0.5 h-0.5 rounded-full bg-muted-foreground/50" />
                        <span>{formatDate(file.modified, i18n.language)}</span>
                    </p>
                </div>
            </div>
        )
    }

    // 渲染文件列表（主 PDF + 其附件 + 无归属附件）
    const renderFileGroup = (files: PdfFile[], depth: number) => {
        const mainFiles = files.filter(f => !f.is_attachment)
        const attachmentFiles = files.filter(f => f.is_attachment)
        const matchedAttachmentPaths = new Set<string>()

        const grouped = mainFiles.map(mainFile => {
            const relatedAttachments = attachmentFiles.filter(att => {
                const matched = isSiblingAttachment(mainFile.name, att.name)
                if (matched) {
                    matchedAttachmentPaths.add(att.path)
                }
                return matched
            })

            const attachmentCount = relatedAttachments.length || mainFile.attachment_count || 0

            return (
                <div key={mainFile.path}>
                    {renderPdfItem(mainFile, depth, attachmentCount)}
                    {relatedAttachments.map(att => renderPdfItem(att, depth + 1, 0, mainFile.path))}
                </div>
            )
        })

        const orphanAttachments = attachmentFiles.filter(att => !matchedAttachmentPaths.has(att.path))

        return (
            <>
                {grouped}
                {orphanAttachments.map(att => renderPdfItem(att, depth + 1))}
            </>
        )
    }

    const renderDirectoryNode = (node: InputDirectoryNode, depth: number = 0) => {
        const isExpanded = expandedFolders.has(node.path)
        const hasContent = node.children.length > 0 || node.files.length > 0

        return (
            <div key={node.path} className="space-y-1">
                <div
                    onClick={() => toggleFolder(node.path)}
                    style={{ marginLeft: `${depth * 14}px` }}
                    className={cn(
                        'flex items-center gap-2 p-2 rounded-lg cursor-pointer transition-all',
                        'hover:bg-gray-100 text-gray-700'
                    )}
                >
                    {isExpanded ? (
                        <ChevronDown className="h-4 w-4 text-gray-400" />
                    ) : (
                        <ChevronRight className="h-4 w-4 text-gray-400" />
                    )}
                    <Folder className="h-4 w-4 text-amber-500" />
                    <span className="text-sm font-medium flex-1 truncate">{node.name}</span>
                    <span className="text-xs text-gray-400">
                        {hasContent ? node.file_count : 0}
                    </span>
                </div>
                {isExpanded && (
                    <div className="space-y-1">
                        {node.children.map(child => renderDirectoryNode(child, depth + 1))}
                        {renderFileGroup(node.files, depth + 1)}
                    </div>
                )}
            </div>
        )
    }

    return (
        <div className="flex h-full bg-[#f5f5f7] isolate overflow-hidden">
            {/* Left Panel: File Explorer */}
            <div className="relative z-20 flex w-80 flex-col bg-transparent" style={{ isolation: 'isolate' }}>
                <div className="p-6">
                    <div className="flex flex-col gap-3">
                        <div className="flex items-center justify-between">
                            <h2 className="text-xl font-semibold tracking-[-0.03em] text-[#1d1d1f]">{t('chat.fileList.title')}</h2>
                            <div className="flex items-center gap-0.5">
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
                                {...folderInputAttributes}
                                multiple
                                onChange={handleFolderSelect}
                                className="hidden"
                            />
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => fileInputRef.current?.click()}
                                disabled={isUploading || isUploadingFolder}
                                className="h-9 w-9 rounded-full transition-all duration-200 hover:bg-[#0071e3]/8 hover:text-[#0071e3]"
                                title={t('chat.fileList.uploadPdf')}
                            >
                                {isUploading ? (
                                    <Loader2 className="h-4 w-4 text-gray-600 animate-spin" />
                                ) : (
                                    <Upload className="h-4 w-4 text-gray-600 transition-colors group-hover:text-[#0071e3]" />
                                )}
                            </Button>
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => folderInputRef.current?.click()}
                                disabled={isUploading || isUploadingFolder}
                                className="h-9 w-9 rounded-full transition-all duration-200 hover:bg-[#0071e3]/8 hover:text-[#0071e3]"
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
                                className="h-9 w-9 rounded-full transition-all duration-200 hover:bg-[#0071e3]/8 hover:text-[#0071e3]"
                                title={t('chat.fileList.refresh')}
                            >
                                <RefreshCw className={cn("h-4 w-4 text-gray-600 transition-colors", loadingFiles && "animate-spin")} />
                            </Button>
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => {
                                    // 全局切换：如果有隐藏的则全部显示，否则全部隐藏
                                    if (hiddenAttachments.size > 0) {
                                        setHiddenAttachments(new Set())
                                    } else {
                                        const allMainPaths = new Set<string>()
                                        const allFiles = fileTree ? collectFiles(fileTree) : []
                                        const mainFiles = allFiles.filter(f => !f.is_attachment)
                                        const attachmentFiles = allFiles.filter(f => f.is_attachment)
                                        mainFiles.forEach(main => {
                                            if (attachmentFiles.some(att =>
                                                getParentDirectory(att.path) === getParentDirectory(main.path)
                                                && isSiblingAttachment(main.name, att.name)
                                            )) {
                                                allMainPaths.add(main.path)
                                            }
                                        })
                                        setHiddenAttachments(allMainPaths)
                                    }
                                }}
                                className={cn(
                                    'h-9 w-9 rounded-full transition-all duration-200 hover:bg-[#0071e3]/8 hover:text-[#0071e3]',
                                    hiddenAttachments.size > 0 && 'bg-black/4 text-[#0071e3]'
                                )}
                                title={hiddenAttachments.size > 0 ? t('chat.fileList.showAttachments') : t('chat.fileList.hideAttachments')}
                            >
                                {hiddenAttachments.size > 0 ? (
                                    <EyeOff className="h-4 w-4 text-gray-400 transition-colors" />
                                ) : (
                                    <Eye className="h-4 w-4 text-gray-600 transition-colors" />
                                )}
                            </Button>
                        </div>
                    </div>
                    </div>
                </div>

                <div
                    className={cn(
                        "flex-1 overflow-y-auto p-3 space-y-2 scrollbar-thin scrollbar-thumb-muted-foreground/10 hover:scrollbar-thumb-muted-foreground/20 transition-colors",
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
                    ) : !fileTree || (fileTree.children.length === 0 && fileTree.files.length === 0) ? (
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
                            {fileTree.children.map((folder) => renderDirectoryNode(folder))}
                            {fileTree.files.length > 0 && fileTree.children.length > 0 && (
                                <div className="border-t border-gray-100 pt-2 mt-2">
                                    <p className="text-xs text-gray-400 px-2 mb-1">{t('chat.fileList.rootDir')}</p>
                                </div>
                            )}
                            {renderFileGroup(fileTree.files, 0)}
                        </>
                    )}
                </div>
            </div>

            {/* Right Panel: Timeline View */}
            <div className="flex-1 flex flex-col relative z-10 bg-white shadow-xl shadow-black/[0.02] rounded-l-[40px] border border-black/5">
                {/* Header */}
                <div className="sticky top-0 z-10 flex h-24 items-center justify-between px-8 bg-white/90 backdrop-blur-[20px] rounded-tl-[40px]">

                    <div className="flex items-center gap-4 relative">
                        <div className="flex flex-col">
                            <h1 className="text-2xl font-semibold tracking-[-0.03em] text-[#1d1d1f]">
                                {t('chat.progress.title')}
                            </h1>
                            <div className="flex items-center gap-2 mt-1.5">
                                <span className={cn(
                                    "w-1.5 h-1.5 rounded-full",
                                    connectionError ? "bg-red-500" :
                                        taskPhase === 'running' ? "bg-purple-500 animate-pulse" :
                                            taskPhase === 'starting' ? "bg-blue-500 animate-pulse" :
                                                taskPhase === 'stopping' ? "bg-amber-500 animate-pulse" :
                                                    taskPhase === 'completed' ? "bg-emerald-500" :
                                                        taskPhase === 'cancelled' ? "bg-amber-500" :
                                                            taskPhase === 'error' ? "bg-red-500" :
                                                                taskPhase === 'ready' ? "bg-blue-400" :
                                                                    "bg-gray-300"
                                )} />
                                <p className={cn(
                                    "text-xs font-medium uppercase tracking-wider",
                                    connectionError || taskPhase === 'error' ? "text-red-500" : "text-gray-500"
                                )}>
                                    {phaseLabel}
                                </p>
                            </div>
                        </div>
                    </div>

                    <div className="flex items-center gap-3 relative">
                        {selectedFileName && (
                            <div className="hidden lg:flex items-center gap-2 px-3 py-2 rounded-xl border border-gray-200 bg-gray-50/80 max-w-64">
                                <FileText className="h-4 w-4 text-gray-500 shrink-0" />
                                <div className="min-w-0">
                                    <p className="text-[11px] uppercase tracking-wider text-gray-400">{t('chat.actions.selected')}</p>
                                    <p className="text-sm font-medium text-gray-700 truncate">{selectedFileName}</p>
                                </div>
                            </div>
                        )}
                        <Button
                            onClick={handleStartTask}
                            disabled={!canStart}
                            className="rounded-xl px-4 bg-gray-900 hover:bg-gray-800 text-white disabled:bg-gray-200 disabled:text-gray-400"
                        >
                            {taskPhase === 'starting' ? (
                                <>
                                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    {t('chat.actions.starting')}
                                </>
                            ) : (
                                t('chat.actions.start')
                            )}
                        </Button>
                        {canResume && (
                            <Button variant="outline" onClick={handleResumeTask} className="rounded-xl px-4">
                                {t('chat.actions.resume')}
                            </Button>
                        )}
                        <Button
                            variant="outline"
                            onClick={handleStopTask}
                            disabled={!canStop}
                            className="rounded-xl px-4 border-amber-200 text-amber-700 hover:bg-amber-50 disabled:border-gray-200 disabled:text-gray-400"
                        >
                            {taskPhase === 'stopping' ? (
                                <>
                                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    {t('chat.actions.stopping')}
                                </>
                            ) : (
                                t('chat.actions.stop')
                            )}
                        </Button>
                        {reconnectAttempts > 0 ? (
                            <div className="flex items-center gap-2 px-3 py-1.5 bg-yellow-50 rounded-lg border border-yellow-200">
                                <Loader2 className="h-3 w-3 text-yellow-600 animate-spin" />
                                <span className="text-xs text-yellow-700 font-medium">
                                    {t('chat.connection.reconnecting', { current: reconnectAttempts, max: 5 })}
                                </span>
                            </div>
                        ) : connectionError ? (
                            <div className="flex items-center gap-2 px-3 py-1.5 bg-red-50 rounded-lg border border-red-200">
                                <span className="w-2 h-2 rounded-full bg-red-500" />
                                <span className="text-xs text-red-600 font-medium">{t('chat.connection.disconnected')}</span>
                            </div>
                        ) : null}
                    </div>
                </div>

                {/* Timeline Area */}
                <div className="flex-1 overflow-y-auto px-6 scrollbar-thin scrollbar-thumb-muted-foreground/10 hover:scrollbar-thumb-muted-foreground/20" style={{ overscrollBehavior: 'contain', willChange: 'scroll-position' }}>
                    <TimelineView
                        events={timelineEvents}
                        taskId={currentTaskId || undefined}
                        batchSummary={batchSummary.total > 0 ? batchSummary : undefined}
                    />
                </div>
            </div>
        </div >
    )
}
