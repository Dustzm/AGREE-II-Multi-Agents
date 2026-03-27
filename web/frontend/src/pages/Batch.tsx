/**
 * Batch - 批量任务运行页面
 * 
 * 选择 input 目录下的文件夹，勾选 PDF 批量执行评价任务
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Toast, type ToastType } from '@/components/Toast'
import {
    Folder, FileText, Play, Square, RefreshCw, Loader2,
    CheckCircle2, XCircle, Clock, AlertCircle, ChevronRight, ChevronDown,
    CheckSquare, Square as SquareIcon, FolderOpen
} from 'lucide-react'
import { cn } from '@/utils/cn'
import {
    batchApi, createBatchWebSocket,
    type BatchDirectoryNode, type PdfInfo, type BatchTask, type BatchStatus, type BatchWebSocketHandle, type ResumeLatestResponse
} from '@/services/batch'
import { api } from '@/services/api'

type TaskStatusType = BatchTask['status']

// 状态配置
const statusConfig: Record<TaskStatusType, { icon: typeof Clock; color: string; bg: string; label: string }> = {
    pending: { icon: Clock, color: 'text-gray-500', bg: 'bg-gray-100', label: '等待中' },
    running: { icon: Loader2, color: 'text-blue-600', bg: 'bg-blue-50', label: '运行中' },
    done: { icon: CheckCircle2, color: 'text-green-600', bg: 'bg-green-50', label: '已完成' },
    error: { icon: XCircle, color: 'text-red-600', bg: 'bg-red-50', label: '失败' },
    cancelled: { icon: AlertCircle, color: 'text-orange-500', bg: 'bg-orange-50', label: '已取消' },
}

// Toast 提示信息
interface ToastMessage {
    id: number
    type: ToastType
    message: string
}

function collectDirectoryPaths(node: BatchDirectoryNode): string[] {
    return node.children.flatMap((child) => [child.path, ...collectDirectoryPaths(child)])
}

function collectDescendantPdfs(node: BatchDirectoryNode): PdfInfo[] {
    return [...node.pdfs, ...node.children.flatMap(collectDescendantPdfs)]
}

export function Batch() {
    const { t } = useTranslation()
    // 文件夹列表
    const [directoryTree, setDirectoryTree] = useState<BatchDirectoryNode | null>(null)
    const [loadingFolders, setLoadingFolders] = useState(true)
    const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())

    // Toast 提示列表
    const [toasts, setToasts] = useState<ToastMessage[]>([])
    const toastIdRef = useRef(0)

    // 勾选状态
    const [selectedPdfs, setSelectedPdfs] = useState<Set<string>>(new Set())

    // 批量任务状态
    const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null)
    const [resumeCandidate, setResumeCandidate] = useState<ResumeLatestResponse | null>(null)
    const [concurrency, setConcurrency] = useState(2)
    const [resumeConcurrency, setResumeConcurrency] = useState(2)
    const [experimentRounds, setExperimentRounds] = useState(1)
    const [isResuming, setIsResuming] = useState(false)

    // WebSocket
    const wsHandleRef = useRef<BatchWebSocketHandle | null>(null)

    // 初始化 sessionId，优先从 localStorage 读取
    // 使用 useState lazy init 来保证只执行一次
    const [sessionId] = useState(() => {
        const saved = localStorage.getItem('batch_session_id')
        if (saved) return saved
        const newId = `batch-${Date.now()}`
        localStorage.setItem('batch_session_id', newId)
        return newId
    })

    const reconnectRef = useRef<(() => void) | null>(null)
    const [wsConnected, setWsConnected] = useState(false)
    const [wsReconnectAttempts, setWsReconnectAttempts] = useState(0)

    // 文件夹上传
    const [isUploadingFolder, setIsUploadingFolder] = useState(false)
    const folderInputRef = useRef<HTMLInputElement>(null)

    // 加载文件夹列表
    const loadFolders = useCallback(async () => {
        setLoadingFolders(true)
        try {
            const data = await batchApi.listFolders()
            setDirectoryTree(data.tree)
            // 默认全部展开
            setExpandedFolders(new Set(collectDirectoryPaths(data.tree)))
        } catch (e) {
            console.error('加载文件夹失败:', e)
        } finally {
            setLoadingFolders(false)
        }
    }, [])

    // 切换文件夹展开
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

    // 勾选/取消单个 PDF
    const togglePdfSelection = useCallback((pdfPath: string) => {
        setSelectedPdfs(prev => {
            const next = new Set(prev)
            if (next.has(pdfPath)) {
                next.delete(pdfPath)
            } else {
                next.add(pdfPath)
            }
            return next
        })
    }, [])

    // 全选/取消某文件夹
    const toggleFolderSelection = useCallback((node: BatchDirectoryNode) => {
        const pdfs = collectDescendantPdfs(node)
        const allSelected = pdfs.every(p => selectedPdfs.has(p.path))

        setSelectedPdfs(prev => {
            const next = new Set(prev)
            pdfs.forEach(p => {
                if (allSelected) {
                    next.delete(p.path)
                } else {
                    next.add(p.path)
                }
            })
            return next
        })
    }, [selectedPdfs])

    // 显示 Toast 提示
    const showToast = useCallback((
        message: string,
        type: ToastType = 'info'
    ) => {
        const id = toastIdRef.current++
        setToasts(prev => [...prev, { id, type, message }])
    }, [])

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
                alert(`上传完成: 成功 ${result.success_count} 个，失败 ${result.failed_count} 个`)
            } else {
                alert(`成功上传 ${result.success_count} 个 PDF 文件`)
            }

            // 刷新文件夹列表
            await loadFolders()
        } catch (error) {
            console.error('文件夹上传失败:', error)
            alert(error instanceof Error ? error.message : '文件夹上传失败')
        } finally {
            setIsUploadingFolder(false)
            // 重置 input
            if (folderInputRef.current) {
                folderInputRef.current.value = ''
            }
        }
    }, [loadFolders])

    const loadResumeCandidate = useCallback(async (hasRunningTask?: boolean) => {
        if (hasRunningTask) {
            setResumeCandidate(null)
            return
        }

        try {
            const candidate = await batchApi.getLatestResumable()
            setResumeCandidate(candidate.available ? candidate : null)
            if (candidate.available && candidate.status?.concurrency) {
                setResumeConcurrency(candidate.status.concurrency)
            }
        } catch (e) {
            console.error('加载可继续任务失败:', e)
            setResumeCandidate(null)
        }
    }, [])

    // 建立/恢复 WebSocket 连接
    const establishWebSocket = useCallback(() => {
        if (wsHandleRef.current) {
            // 如果已有连接且 sessionId 相同，则无需重新建立
            // 这里简单处理：先断开旧的，确保状态一致
            wsHandleRef.current.disconnect()
        }

        const handle = createBatchWebSocket({
            sessionId: sessionId,
            onTaskUpdate: (task: BatchTask) => {
                console.log('收到任务更新:', task)
                setBatchStatus(prev => {
                    if (!prev) {
                        // 如果收到更新但本地还没有状态（可能刚刷新），尝试通过 getStatus 同步
                        // 但这里我们只更新 tasks
                        return prev
                    }
                    // 如果任务列表里没有这个任务（新任务），或者已有任务更新
                    const existingTaskIndex = prev.tasks.findIndex(t => t.id === task.id)
                    const newTasks = [...prev.tasks]

                    if (existingTaskIndex >= 0) {
                        newTasks[existingTaskIndex] = task
                    } else {
                        // 新任务（理论上 startSelected 会返回初始列表，但也可能由其他端触发）
                        newTasks.push(task)
                    }

                    const counts = { pending: 0, running: 0, done: 0, error: 0, cancelled: 0 }
                    newTasks.forEach(t => {
                        const status = t.status
                        if (status in counts) {
                            counts[status as keyof typeof counts]++
                        }
                    })
                    return { ...prev, tasks: newTasks, ...counts }
                })
            },
            onComplete: (status: BatchStatus) => {
                console.log('收到批量状态更新:', status)
                setBatchStatus(status)
                if (!status.is_running) {
                    void loadResumeCandidate(false)
                } else {
                    setResumeCandidate(null)
                }
            },
            onError: (error: string) => {
                console.error('WebSocket 错误:', error)
                // 出错时轮询一次状态
                batchApi.getStatus().then(setBatchStatus).catch(console.error)
            },
            onConnectionChange: (isConnected: boolean, reconnectAttempts: number) => {
                setWsConnected(isConnected)
                setWsReconnectAttempts(reconnectAttempts)
            }
        })
        wsHandleRef.current = handle
        reconnectRef.current = handle.reconnect

        console.log('WebSocket 已建立，sessionId:', sessionId)
    }, [loadResumeCandidate, sessionId])

    // 加载当前状态
    const loadStatus = useCallback(async () => {
        try {
            const status = await batchApi.getStatus()
            setBatchStatus(status)

            // 如果正在运行且没有连接 WS，则建立连接
            if (status.is_running && !wsHandleRef.current) {
                console.log('检测到正在运行的任务，恢复 WebSocket 连接')
                establishWebSocket()
            }
            await loadResumeCandidate(status.is_running)
        } catch (e) {
            console.error('加载状态失败:', e)
        }
    }, [establishWebSocket, loadResumeCandidate])

    // 启动批量任务（使用勾选列表）
    const handleStart = useCallback(async () => {
        if (selectedPdfs.size === 0) return

        try {
            const status = await batchApi.startSelected(Array.from(selectedPdfs), concurrency, experimentRounds)
            setBatchStatus(status)
            setResumeCandidate(null)

            // 建立 WebSocket 连接
            establishWebSocket()
            console.log('WebSocket 已创建，sessionId:', sessionId)
        } catch (e) {
            alert(e instanceof Error ? e.message : '启动失败')
        }
    }, [selectedPdfs, concurrency, experimentRounds, sessionId, establishWebSocket])

    const handleResumeLatest = useCallback(async () => {
        setIsResuming(true)
        try {
            const status = await batchApi.resumeLatest(resumeConcurrency)
            setBatchStatus(status)
            setResumeCandidate(null)
            establishWebSocket()
            showToast(t('batch.resume.success'), 'success')
        } catch (e) {
            showToast(
                e instanceof Error ? e.message : t('batch.resume.failed'),
                'error'
            )
        } finally {
            setIsResuming(false)
            await loadResumeCandidate(false)
        }
    }, [establishWebSocket, loadResumeCandidate, resumeConcurrency, showToast, t])

    // 停止批量任务
    const handleStop = useCallback(async () => {
        if (!batchStatus?.is_running) {
            showToast('当前没有正在运行的批量任务', 'warning')
            return
        }

        try {
            await batchApi.stop()
            await loadStatus()

            showToast(
                '批量任务已停止，正在等待当前步骤完成...',
                'success'
            )
        } catch (e) {
            showToast(
                e instanceof Error ? e.message : '停止失败',
                'error'
            )
        }
    }, [batchStatus, loadStatus, showToast])

    // 初始化
    useEffect(() => {
        loadFolders()
        loadStatus()

        return () => {
            wsHandleRef.current?.disconnect()
        }
    }, [loadFolders, loadStatus])

    // 页面可见性监听 - 返回页面时自动重连 WebSocket
    useEffect(() => {
        const handleVisibilityChange = () => {
            if (document.visibilityState === 'visible' &&
                !wsConnected &&
                batchStatus?.is_running &&
                wsHandleRef.current &&
                reconnectRef.current) {
                console.log('页面重新可见，尝试重连 WebSocket')
                reconnectRef.current()
            }
        }

        document.addEventListener('visibilitychange', handleVisibilityChange)
        return () => {
            document.removeEventListener('visibilitychange', handleVisibilityChange)
        }
    }, [wsConnected, batchStatus?.is_running])

    const isRunning = batchStatus?.is_running ?? false
    const resumableStatus = resumeCandidate?.status ?? null
    const resumableRound = resumeCandidate?.resume_round ?? resumableStatus?.current_round ?? 0
    const resumableIncompleteCount = resumeCandidate?.remaining_tasks ?? 0
    const directoryInputProps: Record<string, string> = { webkitdirectory: '', directory: '' }

    // 格式化文件大小
    const formatSize = (bytes: number) => {
        if (bytes < 1024) return `${bytes} B`
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    }

    // 解析后端通过 i18n: 前缀发送的状态消息
    const getStatusMessage = (step: string | null) => {
        if (!step) return null

        // 如果不是 i18n 消息，直接返回
        if (!step.startsWith('i18n:')) return step

        try {
            const parts = step.split(':')
            const key = parts[1] // e.g. batch.initializing
            const args = parts.slice(2)

            // Map backend keys to frontend translation keys (under batch.statusMsg)
            const fieldName = key.split('.')[1]
            const transKey = `batch.statusMsg.${fieldName}`

            if (key === 'batch.retrying') {
                return t(transKey, { current: args[0], max: args[1] })
            }
            if (key === 'batch.failed') {
                return t(transKey, { error: args.join(':') })
            }

            return t(transKey)
        } catch {
            console.warn('Failed to parse i18n message:', step)
            return step
        }
    }

    // 计算文件夹选中状态
    const getFolderCheckState = (node: BatchDirectoryNode): 'none' | 'some' | 'all' => {
        const pdfs = collectDescendantPdfs(node)
        if (pdfs.length === 0) return 'none'
        const selectedCount = pdfs.filter(p => selectedPdfs.has(p.path)).length
        if (selectedCount === 0) return 'none'
        if (selectedCount === pdfs.length) return 'all'
        return 'some'
    }

    const renderPdfRow = (pdf: PdfInfo, depth: number) => {
        const isSelected = selectedPdfs.has(pdf.path)
        const attachmentCount = pdf.attachment_count || 0

        return (
            <div
                key={pdf.path}
                onClick={() => !isRunning && togglePdfSelection(pdf.path)}
                style={{ marginLeft: `${depth * 14}px` }}
                className={cn(
                    'flex items-center gap-2 p-2 rounded-lg transition-all cursor-pointer',
                    isSelected ? 'bg-blue-50 border border-blue-200' : 'hover:bg-gray-50',
                    isRunning && 'opacity-50 cursor-not-allowed'
                )}
            >
                {isSelected ? (
                    <CheckSquare className="h-4 w-4 text-blue-600 shrink-0" />
                ) : (
                    <SquareIcon className="h-4 w-4 text-gray-300 shrink-0" />
                )}
                <FileText className="h-4 w-4 text-red-400 shrink-0" />
                <span className="text-sm text-gray-700 truncate flex-1">{pdf.name}</span>
                {attachmentCount > 0 && (
                    <span
                        className="px-1.5 py-0.5 text-[10px] font-medium rounded-full bg-amber-100 text-amber-700 shrink-0"
                        title={`${attachmentCount} 个附件`}
                    >
                        +{attachmentCount}
                    </span>
                )}
                <span className="text-xs text-gray-400 shrink-0">{formatSize(pdf.size)}</span>
            </div>
        )
    }

    const renderDirectoryNode = (node: BatchDirectoryNode, depth: number = 0) => {
        const isExpanded = expandedFolders.has(node.path)
        const checkState = getFolderCheckState(node)
        const hasChildren = node.children.length > 0 || node.pdfs.length > 0

        return (
            <div key={node.path} className="space-y-1">
                <div
                    style={{ marginLeft: `${depth * 14}px` }}
                    className={cn(
                        'flex items-center gap-2 p-2 rounded-lg transition-all',
                        'hover:bg-gray-100 cursor-pointer',
                        isRunning && 'opacity-50 cursor-not-allowed'
                    )}
                >
                    <div
                        onClick={() => !isRunning && toggleFolder(node.path)}
                        className="p-1"
                    >
                        {isExpanded ? (
                            <ChevronDown className="h-4 w-4 text-gray-400" />
                        ) : (
                            <ChevronRight className="h-4 w-4 text-gray-400" />
                        )}
                    </div>
                    <div
                        onClick={() => !isRunning && toggleFolderSelection(node)}
                        className="p-1"
                    >
                        {checkState === 'all' ? (
                            <CheckSquare className="h-4 w-4 text-blue-600" />
                        ) : checkState === 'some' ? (
                            <div className="h-4 w-4 border-2 border-blue-600 rounded flex items-center justify-center">
                                <div className="w-2 h-0.5 bg-blue-600" />
                            </div>
                        ) : (
                            <SquareIcon className="h-4 w-4 text-gray-300" />
                        )}
                    </div>
                    <Folder className="h-4 w-4 text-amber-500" />
                    <span className="text-sm font-medium flex-1 text-gray-700 truncate">{node.name}</span>
                    <span className="text-xs text-gray-400">{node.pdf_count}</span>
                </div>

                {isExpanded && (
                    <div className="space-y-1">
                        {node.children.map((child) => renderDirectoryNode(child, depth + 1))}
                        {node.pdfs.length === 0 && node.children.length === 0 && hasChildren === false ? (
                            <div
                                style={{ marginLeft: `${(depth + 1) * 14}px` }}
                                className="p-2 text-xs text-gray-400"
                            >
                                {t('batch.empty.pdf')}
                            </div>
                        ) : (
                            node.pdfs.map((pdf) => renderPdfRow(pdf, depth + 1))
                        )}
                    </div>
                )}
            </div>
        )
    }

    return (
        <div className="flex h-full bg-gradient-to-br from-gray-50 via-gray-50 to-gray-100">
            {/* 左侧：文件夹 + PDF 层级 */}
            <div className="w-96 border-r border-gray-200/60 bg-white/95 flex flex-col shadow-[4px_0_24px_-12px_rgba(0,0,0,0.05)]">
                <div className="p-6 border-b border-gray-100">
                    <div className="flex items-center justify-between">
                        <div>
                            <h2 className="text-lg font-bold text-gray-800">{t('batch.select.title')}</h2>
                            <p className="text-xs text-gray-500 mt-1">
                                {t('batch.select.count', { count: selectedPdfs.size })}
                            </p>
                        </div>
                        <div className="flex items-center gap-2">
                            <input
                                ref={folderInputRef}
                                type="file"
                                {...directoryInputProps}
                                multiple
                                onChange={handleFolderSelect}
                                className="hidden"
                            />
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => folderInputRef.current?.click()}
                                disabled={isRunning || isUploadingFolder}
                                className="h-9 w-9 rounded-xl transition-all duration-300 hover:bg-gradient-to-br hover:from-amber-500/20 hover:to-orange-500/20 hover:shadow-lg hover:shadow-amber-500/10 hover:scale-110 hover:backdrop-blur-sm active:scale-95 cursor-pointer"
                                title={t('batch.uploadFolder')}
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
                                onClick={loadFolders}
                                disabled={loadingFolders || isUploadingFolder}
                                className="h-9 w-9 rounded-xl transition-all duration-300 hover:bg-gradient-to-br hover:from-blue-500/20 hover:to-cyan-500/20 hover:shadow-lg hover:shadow-blue-500/10 hover:scale-110 hover:backdrop-blur-sm active:scale-95 cursor-pointer"
                            >
                                <RefreshCw className={cn("h-4 w-4 text-gray-600 transition-colors", loadingFolders && "animate-spin")} />
                            </Button>
                        </div>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto p-3 space-y-1">
                    {loadingFolders ? (
                        <div className="flex items-center justify-center py-10 text-gray-500">
                            <Loader2 className="h-5 w-5 animate-spin mr-2" />
                            <span className="text-sm">{t('common.loading')}</span>
                        </div>
                    ) : !directoryTree || (directoryTree.children.length === 0 && directoryTree.pdfs.length === 0) ? (
                        <div className="text-center py-10 text-gray-400">
                            <Folder className="h-10 w-10 mx-auto mb-2 opacity-40" />
                            <p className="text-sm">{t('batch.empty.folder')}</p>
                        </div>
                    ) : (
                        <>
                            {directoryTree.children.map((folder) => renderDirectoryNode(folder))}
                            {directoryTree.pdfs.length > 0 && (
                                <div className="pt-2 mt-2 border-t border-gray-100 space-y-1">
                                    <p className="px-2 text-xs text-gray-400">input</p>
                                    {directoryTree.pdfs.map((pdf) => renderPdfRow(pdf, 0))}
                                </div>
                            )}
                        </>
                    )}
                </div>

                {/* 控制区 */}
                <div className="p-4 border-t border-gray-100 space-y-4">
                    <div className="flex items-center gap-6">
                        <div className="flex items-center gap-3">
                            <label className="text-sm text-gray-600">{t('batch.concurrency.label')}</label>
                            <input
                                type="number"
                                min={1}
                                max={10}
                                value={concurrency}
                                onChange={(e) => setConcurrency(Math.max(1, Math.min(10, parseInt(e.target.value) || 1)))}
                                disabled={isRunning}
                                className="w-16 px-2 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50"
                            />
                        </div>
                        <div className="flex items-center gap-3">
                            <label className="text-sm text-gray-600">{t('batch.rounds.label')}</label>
                            <input
                                type="number"
                                min={1}
                                max={4}
                                value={experimentRounds}
                                onChange={(e) => setExperimentRounds(Math.max(1, Math.min(4, parseInt(e.target.value) || 1)))}
                                disabled={isRunning}
                                className="w-16 px-2 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50"
                            />
                        </div>
                    </div>

                    <div className="flex gap-2">
                        <Button
                            onClick={handleStart}
                            disabled={selectedPdfs.size === 0 || isRunning}
                            className="flex-1 bg-gradient-to-r from-blue-500 to-indigo-500 hover:from-blue-600 hover:to-indigo-600"
                        >
                            <Play className="h-4 w-4 mr-2" />
                            {t('batch.button.start')} ({selectedPdfs.size})
                        </Button>
                        <Button
                            variant="outline"
                            onClick={handleStop}
                            disabled={!isRunning}
                            className="border-red-200 text-red-600 hover:bg-red-50"
                        >
                            <Square className="h-4 w-4 mr-2" />
                            {t('batch.button.stop')}
                        </Button>
                    </div>
                </div>
            </div>

            {/* 右侧：任务状态 */}
            <div className="flex-1 flex flex-col">
                {/* 统计头部 */}
                <div className="p-6 border-b border-gray-200/50 bg-white/95">
                    <div className="flex items-center justify-between">
                        <div>
                            <h2 className="text-lg font-bold text-gray-800">{t('batch.status.title')}</h2>
                            <p className="text-xs text-gray-500 mt-1">
                                {isRunning ? (
                                    batchStatus && batchStatus.experiment_rounds > 1
                                        ? `${t('batch.status.running')} - ${t('batch.status.round', { current: batchStatus.current_round, total: batchStatus.experiment_rounds })}`
                                        : t('batch.status.running')
                                ) : batchStatus?.total ? t('batch.status.completed') : t('batch.status.waiting')}
                            </p>
                        </div>
                        <div className="flex items-center gap-4">
                            {batchStatus && batchStatus.total > 0 && (
                                <div className="flex items-center gap-4 text-sm">
                                    <div className="flex items-center gap-1.5">
                                        <span className="w-2 h-2 rounded-full bg-green-500" />
                                        <span className="text-gray-600">{batchStatus.done}</span>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                                        <span className="text-gray-600">{batchStatus.running}</span>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        <span className="w-2 h-2 rounded-full bg-gray-300" />
                                        <span className="text-gray-600">{batchStatus.pending}</span>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        <span className="w-2 h-2 rounded-full bg-red-500" />
                                        <span className="text-gray-600">{batchStatus.error}</span>
                                    </div>
                                </div>
                            )}
                            {wsReconnectAttempts > 0 ? (
                                <div className="flex items-center gap-2 px-3 py-1.5 bg-yellow-50 rounded-lg border border-yellow-200">
                                    <Loader2 className="h-3 w-3 text-yellow-600 animate-spin" />
                                    <span className="text-xs text-yellow-700 font-medium">
                                        重连中 ({wsReconnectAttempts}/5)
                                    </span>
                                </div>
                            ) : !wsConnected && batchStatus?.is_running ? (
                                <div className="flex items-center gap-2 px-3 py-1.5 bg-red-50 rounded-lg border border-red-200">
                                    <span className="w-2 h-2 rounded-full bg-red-500" />
                                    <span className="text-xs text-red-600 font-medium">连接断开</span>
                                </div>
                            ) : null}
                        </div>
                    </div>
                </div>

                {/* 任务卡片列表 */}
                <div className="flex-1 overflow-y-auto p-6">
                    {!isRunning && resumableStatus && (
                        <div className="mb-4 rounded-2xl border border-amber-200 bg-gradient-to-r from-amber-50 to-orange-50 p-4">
                            <div className="flex items-start justify-between gap-4">
                                <div>
                                    <div className="flex items-center gap-2 text-amber-700">
                                        <AlertCircle className="h-4 w-4" />
                                        <span className="text-sm font-semibold">{t('batch.resume.title')}</span>
                                    </div>
                                    <p className="mt-2 text-sm text-slate-700">
                                        {t('batch.resume.description', {
                                            current: resumableRound,
                                            total: resumableStatus.experiment_rounds,
                                        })}
                                    </p>
                                    <div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-600">
                                        <span>{t('batch.resume.total', { count: resumableStatus.total })}</span>
                                        <span>{t('batch.resume.done', { count: resumableStatus.done })}</span>
                                        <span>{t('batch.resume.remaining', { count: resumableIncompleteCount })}</span>
                                        {resumeCandidate?.last_updated && (
                                            <span>{t('batch.resume.updated', { time: resumeCandidate.last_updated.replace('T', ' ').slice(0, 16) })}</span>
                                        )}
                                    </div>
                                </div>
                                <div className="shrink-0 flex items-end gap-2">
                                    <div className="flex flex-col gap-1">
                                        <label className="text-xs font-medium text-amber-800">
                                            {t('batch.resume.concurrency')}
                                        </label>
                                        <input
                                            type="number"
                                            min={1}
                                            max={10}
                                            value={resumeConcurrency}
                                            onChange={(e) => setResumeConcurrency(Math.max(1, Math.min(10, parseInt(e.target.value) || 1)))}
                                            disabled={isResuming}
                                            className="w-16 rounded-lg border border-amber-200 bg-white px-2 py-1.5 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-amber-500/20 disabled:opacity-50"
                                        />
                                    </div>
                                    <Button
                                        onClick={handleResumeLatest}
                                        disabled={isResuming}
                                        className="bg-amber-600 text-white hover:bg-amber-700"
                                    >
                                        {isResuming ? (
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        ) : (
                                            <Play className="mr-2 h-4 w-4" />
                                        )}
                                        {t('batch.resume.action')}
                                    </Button>
                                </div>
                            </div>
                            <p className="mt-3 text-xs text-amber-800/80">
                                {t('batch.resume.concurrencyHint', { count: resumableStatus.concurrency })}
                            </p>
                        </div>
                    )}

                    {!batchStatus || batchStatus.tasks.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-gray-400">
                            <Clock className="h-12 w-12 mb-3 opacity-30" />
                            <p className="text-sm">{t('batch.status.hint')}</p>
                        </div>
                    ) : (
                        <div className="grid gap-3">
                            {batchStatus.tasks.map((task) => {
                                const config = statusConfig[task.status]
                                const StatusIcon = config.icon

                                return (
                                    <div
                                        key={task.id}
                                        className={cn(
                                            "flex items-center gap-4 p-4 rounded-xl border transition-all",
                                            config.bg,
                                            task.status === 'running' ? "border-blue-200 shadow-sm" : "border-transparent"
                                        )}
                                    >
                                        <div className={cn("p-2 rounded-lg", config.bg)}>
                                            <StatusIcon className={cn(
                                                "h-5 w-5",
                                                config.color,
                                                task.status === 'running' && "animate-spin"
                                            )} />
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <h4 className="text-sm font-medium text-gray-800 truncate">
                                                {task.id}
                                            </h4>
                                            <p className={cn("text-xs mt-0.5 truncate", config.color)}>
                                                {getStatusMessage(task.current_step) || config.label}
                                                {task.retry_count > 0 && ` (重试 ${task.retry_count} 次)`}
                                            </p>
                                        </div>
                                        {task.status === 'error' && task.error && (
                                            <div className="text-xs text-red-500 max-w-48 truncate" title={task.error}>
                                                {getStatusMessage(task.error)}
                                            </div>
                                        )}
                                    </div>
                                )
                            })}
                        </div>
                    )}
                </div>
            </div>

            {/* Toast 提示列表 */}
            {toasts.map(toast => (
                <Toast
                    key={toast.id}
                    type={toast.type}
                    message={toast.message}
                    onClose={() => {
                        setToasts(prev => prev.filter(t => t.id !== toast.id))
                    }}
                />
            ))}
        </div>
    )
}
