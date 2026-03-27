/**
 * Chat - 评价任务工作区
 * 
 * 使用时序图展示评价过程，支持文件夹层级展示
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { FileText, Loader2, RefreshCw, Sparkles, Upload, Folder, ChevronRight, ChevronDown, FolderOpen, Paperclip, Eye, EyeOff, FileSpreadsheet, FileImage, Film, Music, FileCode, File, Presentation } from 'lucide-react'
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

type TaskPhase = 'idle' | 'ready' | 'starting' | 'running' | 'stopping' | 'completed' | 'cancelled' | 'error'
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
    const { t } = useTranslation()
    // 持久化状态初始化
    const [sessionId] = useState(() => {
        const saved = localStorage.getItem('ama_session_id')
        if (saved) return saved
        const newId = `session-${Date.now()}`
        localStorage.setItem('ama_session_id', newId)
        return newId
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

                // 如果本地没有记录，或者记录不一致（服务重启），则清理缓存
                if (localStartup && localStartup !== serverStartup) {
                    console.log('检测到服务重启，清理旧缓存...')
                    // 清理所有相关缓存
                    localStorage.removeItem('ama_session_id')
                    localStorage.removeItem('ama_is_loading')
                    localStorage.removeItem('ama_task_phase')
                    localStorage.removeItem('ama_current_file')
                    localStorage.removeItem('ama_task_id')
                    localStorage.removeItem('ama_timeline_events')
                    localStorage.removeItem('ama_batch_summary')

                    // 更新启动时间
                    localStorage.setItem('ama_startup_time', serverStartup)

                    // 重新加载页面以重置状态
                    window.location.reload()
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
            alert(t('chat.error.pdfOnly'))
            return
        }

        setIsUploading(true)
        try {
            await api.uploadPdfToInput(file)
            await loadPdfFiles()  // 刷新文件列表
        } catch (error) {
            console.error('Upload failed:', error)
            alert(error instanceof Error ? error.message : t('chat.error.uploadFailed'))
        } finally {
            setIsUploading(false)
        }
    }, [loadPdfFiles, t])

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
            console.error('Folder upload failed:', error)
            alert(error instanceof Error ? error.message : t('chat.error.folderUploadFailed'))
        } finally {
            setIsUploadingFolder(false)
            // 重置 input
            if (folderInputRef.current) {
                folderInputRef.current.value = ''
            }
        }
    }, [loadPdfFiles, t])

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

    const { connect, send, reconnectAttempts } = useWebSocket({
        onOpen: () => {
            setConnectionError(false)
        },
        onMessage: (msg: ChatMessage) => {
            if (msg.task_id) {
                setCurrentTaskId(msg.task_id)
            }

            if (msg.type === 'status' || msg.type === 'agent' || msg.type === 'item_progress' || msg.type === 'batch_summary') {
                setTaskPhase(prev => prev === 'starting' ? 'running' : prev)
            }

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
                const content = msg.content || t('chat.status.processing', { agent: msg.agent })

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
                            // 移除消息末尾的 "..."
                            const doneMessage = e.message.endsWith('...')
                                ? e.message.replace(/\.\.\.$/, '')
                                : e.message
                            return { ...e, status: 'done' as const, message: doneMessage }
                        }
                        // 如果是同类型（Appraiser）但不同 item，保持运行（并发模式下多个可同时运行）
                        return e
                    })
                    return updated
                })

                upsertEvent(eventId, eventType, 'running', content, detail, msg.prompt)

            } else if (msg.type === 'status') {
                const content = msg.content || ''
                const contentLower = content.toLowerCase()

                // 检测 PDF 解析状态 (Bilingual)
                // ZH: 正在解析 PDF... / PDF 解析完成
                // EN: Parsing PDF... / PDF parsed
                if (content.includes('解析') || content.includes('PDF') || content.includes('Parsing') || content.includes('Parsed')) {
                    if (content.includes('完成') || content.includes('parsed') || content.includes('found')) {
                        upsertEvent('parsing', 'parsing', 'done', content)
                    } else if (content.includes('失败') || content.includes('failed')) {
                        upsertEvent('parsing', 'parsing', 'error', content)
                    } else {
                        upsertEvent('parsing', 'parsing', 'running', content)
                    }
                }
                // 检测语义来源池状态 (Bilingual, case-insensitive)
                // ZH: 正在构建语义来源池... / 语义来源池构建完成 / 预处理异常
                // EN: Building semantic source pool... / Semantic source pool built / Preprocessing error
                // Use toLowerCase() for case-insensitive matching
                else if (content.includes('语义来源池') || contentLower.includes('source pool') || contentLower.includes('evidence') || contentLower.includes('preprocessing') || content.includes('预处理异常')) {
                    if (content.includes('正在') || contentLower.includes('building')) {
                        upsertEvent('preprocessing', 'preprocessing', 'running', content)
                    } else if (content.includes('完成') || contentLower.includes('built')) {
                        upsertEvent('preprocessing', 'preprocessing', 'done', content)
                    } else if (content.includes('失败') || content.includes('异常') || contentLower.includes('error') || contentLower.includes('failed')) {
                        upsertEvent('preprocessing', 'preprocessing', 'error', content)
                    } else {
                        // 默认 fallback 用于显示其他预处理相关消息
                        upsertEvent('preprocessing', 'preprocessing', 'running', content)
                    }
                }
                // 检测 Orchestrator 启动 (Bilingual)
                // ZH: 启动智能体团队
                // EN: Starting agent team
                else if (content.includes('智能体') || content.includes('启动') || content.includes('Starting agent')) {
                    upsertEvent('orchestrator', 'orchestrator', 'running', t('chat.status.orchestrating'))
                }
                // 检测任务 ID
                else if (content.includes('任务')) {
                    // 静默处理，不添加事件
                }
                if (content.includes('终止') || contentLower.includes('stopping')) {
                    setTaskPhase('stopping')
                }

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
                upsertEvent('complete', 'complete', 'done', t('chat.status.complete'))

            } else if (msg.type === 'cancelled') {
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
                upsertEvent('cancelled', 'cancelled', 'done', t('chat.status.cancelled'))

            } else if (msg.type === 'error') {
                setTaskPhase(prev => (
                    prev === 'starting' || prev === 'running' || prev === 'stopping'
                        ? 'error'
                        : prev
                ))
                upsertEvent('error', 'error', 'error', msg.content || t('chat.error.occurred'))

            } else if (msg.type === 'item_progress') {
                // 并发评价单条目进度
                const { skill_id, title, item_status, duration } = msg
                if (!skill_id) return

                const eventId = `appraiser_${skill_id}`

                if (item_status === 'started') {
                    // 新条目开始，添加事件（带渐入动画）
                    setTimelineEvents(prev => {
                        const existing = prev.find(e => e.id === eventId)
                        if (existing) return prev  // 已存在则不重复添加
                        return [...prev, {
                            id: eventId,
                            type: 'appraiser',
                            status: 'running',
                            message: t('timeline.evaluating', { title: title || skill_id }),
                            detail: skill_id,
                            timestamp: new Date(),
                            isNew: true
                        }]
                    })
                } else if (item_status === 'completed') {
                    // 条目完成（不显示"已完成"文字）
                    setTimelineEvents(prev => prev.map(e =>
                        e.id === eventId
                            ? { ...e, status: 'done' as const, message: t('timeline.evaluated', { title: title || skill_id }), duration, isNew: false }
                            : e
                    ))
                } else if (item_status === 'failed') {
                    // 条目失败
                    setTimelineEvents(prev => prev.map(e =>
                        e.id === eventId
                            ? { ...e, status: 'error' as const, message: t('timeline.evaluateFailed', { title: title || skill_id }), isNew: false }
                            : e
                    ))
                }

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
        upsertEvent('parsing', 'parsing', 'running', t('chat.status.parsing'))

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
            upsertEvent('error', 'error', 'error', t('chat.error.startFailed', { error: String(error) }))
            setTaskPhase('error')
        }
    }, [clearTaskState, connect, currentFile, isBusy, send, sessionId, t, upsertEvent])

    const handleStopTask = useCallback(() => {
        if (!currentTaskId || (taskPhase !== 'starting' && taskPhase !== 'running')) return

        setTaskPhase('stopping')
        setConnectionError(false)

        const currentLang = document.documentElement.lang || 'en'
        const lang = currentLang.split('-')[0]
        send({ type: 'cancel', task_id: currentTaskId, lang })
    }, [currentTaskId, send, taskPhase])

    const selectedFileName = currentFile?.split('/').pop() ?? null
    const canStart = Boolean(currentFile) && ['ready', 'completed', 'cancelled', 'error'].includes(taskPhase)
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
            case 'cancelled':
                return t('chat.progress.cancelled')
            case 'error':
                return t('chat.progress.error')
            default:
                return t('chat.progress.ready')
        }
    })()

    const formatFileSize = (bytes: number) => {
        if (bytes < 1024) return `${bytes} B`
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
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
                            <span>{file.modified}</span>
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
                        <span>{file.modified}</span>
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
                                    "h-9 w-9 rounded-xl transition-all duration-300 hover:bg-gradient-to-br hover:from-gray-500/20 hover:to-slate-500/20 hover:shadow-lg hover:shadow-gray-500/10 hover:scale-110 hover:backdrop-blur-sm active:scale-95 cursor-pointer",
                                    hiddenAttachments.size > 0 && "bg-gray-100"
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
                                    重连中 ({reconnectAttempts}/5)
                                </span>
                            </div>
                        ) : connectionError ? (
                            <div className="flex items-center gap-2 px-3 py-1.5 bg-red-50 rounded-lg border border-red-200">
                                <span className="w-2 h-2 rounded-full bg-red-500" />
                                <span className="text-xs text-red-600 font-medium">连接断开</span>
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
