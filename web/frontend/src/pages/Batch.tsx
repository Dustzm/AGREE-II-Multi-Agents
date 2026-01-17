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
    type FolderInfo, type PdfInfo, type BatchTask, type BatchStatus
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

export function Batch() {
    const { t } = useTranslation()
    // 文件夹列表
    const [folders, setFolders] = useState<FolderInfo[]>([])
    const [loadingFolders, setLoadingFolders] = useState(true)
    const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())

    // Toast 提示列表
    const [toasts, setToasts] = useState<ToastMessage[]>([])
    const toastIdRef = useRef(0)

    // PDF 列表 (每个文件夹)
    const [folderPdfs, setFolderPdfs] = useState<Record<string, PdfInfo[]>>({})
    const [loadingPdfs, setLoadingPdfs] = useState<Set<string>>(new Set())

    // 勾选状态
    const [selectedPdfs, setSelectedPdfs] = useState<Set<string>>(new Set())

    // 批量任务状态
    const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null)
    const [concurrency, setConcurrency] = useState(2)
    const [experimentRounds, setExperimentRounds] = useState(1)

    // WebSocket
    const wsRef = useRef<WebSocket | null>(null)
    const sessionIdRef = useRef(`batch-${Date.now()}`)

    // 文件夹上传
    const [isUploadingFolder, setIsUploadingFolder] = useState(false)
    const folderInputRef = useRef<HTMLInputElement>(null)

    // 加载文件夹列表
    const loadFolders = useCallback(async () => {
        setLoadingFolders(true)
        try {
            const data = await batchApi.listFolders()
            setFolders(data)
            // 默认全部展开
            setExpandedFolders(new Set(data.map(f => f.name)))
        } catch (e) {
            console.error('加载文件夹失败:', e)
        } finally {
            setLoadingFolders(false)
        }
    }, [])

    // 加载 PDF 列表
    const loadPdfs = useCallback(async (folderName: string) => {
        if (folderPdfs[folderName]) return // 已加载过

        setLoadingPdfs(prev => new Set(prev).add(folderName))
        try {
            const data = await batchApi.listPdfs(folderName)
            setFolderPdfs(prev => ({ ...prev, [folderName]: data }))
        } catch (e) {
            console.error('加载 PDF 列表失败:', e)
        } finally {
            setLoadingPdfs(prev => {
                const next = new Set(prev)
                next.delete(folderName)
                return next
            })
        }
    }, [folderPdfs])

    // 切换文件夹展开
    const toggleFolder = useCallback((folderName: string) => {
        setExpandedFolders(prev => {
            const next = new Set(prev)
            if (next.has(folderName)) {
                next.delete(folderName)
            } else {
                next.add(folderName)
                // 展开时加载 PDF
                loadPdfs(folderName)
            }
            return next
        })
    }, [loadPdfs])

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
    const toggleFolderSelection = useCallback((folderName: string) => {
        const pdfs = folderPdfs[folderName] || []
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
    }, [folderPdfs, selectedPdfs])

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

    // 加载当前状态
    const loadStatus = useCallback(async () => {
        try {
            const status = await batchApi.getStatus()
            setBatchStatus(status)
        } catch (e) {
            console.error('加载状态失败:', e)
        }
    }, [])

    // 启动批量任务（使用勾选列表）
    const handleStart = useCallback(async () => {
        if (selectedPdfs.size === 0) return

        try {
            const status = await batchApi.startSelected(Array.from(selectedPdfs), concurrency, experimentRounds)
            setBatchStatus(status)

            // 建立 WebSocket 连接
            if (wsRef.current) {
                wsRef.current.close()
            }
            wsRef.current = createBatchWebSocket(
                sessionIdRef.current,
                (task) => {
                    console.log('收到任务更新:', task)
                    // 更新单个任务
                    setBatchStatus(prev => {
                        if (!prev) {
                            console.warn('收到任务更新但 batchStatus 为空')
                            return prev
                        }
                        const tasks = prev.tasks.map(t =>
                            t.id === task.id ? task : t
                        )
                        // 重新计算统计
                        const counts = { pending: 0, running: 0, done: 0, error: 0, cancelled: 0 }
                        tasks.forEach(t => counts[t.status]++)
                        return { ...prev, tasks, ...counts }
                    })
                },
                (status) => {
                    console.log('收到批量状态更新:', status)
                    setBatchStatus(status)
                },
                (error) => {
                    console.error('WebSocket 错误:', error)
                    // WebSocket错误时降级到轮询
                    const interval = setInterval(async () => {
                        try {
                            const latestStatus = await batchApi.getStatus()
                            setBatchStatus(latestStatus)
                        } catch (e) {
                            console.error('轮询状态失败:', e)
                        }
                    }, 3000)

                    // 清理定时器（当组件卸载或任务完成时）
                    wsRef.current?.addEventListener('close', () => clearInterval(interval), { once: true })
                }
            )

            console.log('WebSocket 已创建，sessionId:', sessionIdRef.current)
        } catch (e) {
            alert(e instanceof Error ? e.message : '启动失败')
        }
    }, [selectedPdfs, concurrency, experimentRounds])

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
            wsRef.current?.close()
        }
    }, [loadFolders, loadStatus])

    // 展开文件夹时加载 PDF
    useEffect(() => {
        expandedFolders.forEach(folderName => {
            if (!folderPdfs[folderName]) {
                loadPdfs(folderName)
            }
        })
    }, [expandedFolders, folderPdfs, loadPdfs])

    const isRunning = batchStatus?.is_running ?? false

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
        } catch (e) {
            console.warn('Failed to parse i18n message:', step)
            return step
        }
    }

    // 计算文件夹选中状态
    const getFolderCheckState = (folderName: string): 'none' | 'some' | 'all' => {
        const pdfs = folderPdfs[folderName] || []
        if (pdfs.length === 0) return 'none'
        const selectedCount = pdfs.filter(p => selectedPdfs.has(p.path)).length
        if (selectedCount === 0) return 'none'
        if (selectedCount === pdfs.length) return 'all'
        return 'some'
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
                                {...({ webkitdirectory: '', directory: '' } as any)}
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
                    ) : folders.length === 0 ? (
                        <div className="text-center py-10 text-gray-400">
                            <Folder className="h-10 w-10 mx-auto mb-2 opacity-40" />
                            <p className="text-sm">{t('batch.empty.folder')}</p>
                        </div>
                    ) : (
                        folders.map((folder) => {
                            const isExpanded = expandedFolders.has(folder.name)
                            const checkState = getFolderCheckState(folder.name)
                            const pdfs = folderPdfs[folder.name] || []
                            const isLoadingPdfs = loadingPdfs.has(folder.name)

                            return (
                                <div key={folder.path} className="space-y-1">
                                    {/* 文件夹行 */}
                                    <div
                                        className={cn(
                                            "flex items-center gap-2 p-2 rounded-lg transition-all",
                                            "hover:bg-gray-100 cursor-pointer",
                                            isRunning && "opacity-50 cursor-not-allowed"
                                        )}
                                    >
                                        {/* 展开/折叠 */}
                                        <div
                                            onClick={() => !isRunning && toggleFolder(folder.name)}
                                            className="p-1"
                                        >
                                            {isExpanded ? (
                                                <ChevronDown className="h-4 w-4 text-gray-400" />
                                            ) : (
                                                <ChevronRight className="h-4 w-4 text-gray-400" />
                                            )}
                                        </div>
                                        {/* 勾选框 */}
                                        <div
                                            onClick={() => !isRunning && toggleFolderSelection(folder.name)}
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
                                        <span className="text-sm font-medium flex-1 text-gray-700">{folder.name}</span>
                                        <span className="text-xs text-gray-400">{folder.pdf_count}</span>
                                    </div>

                                    {/* PDF 列表 */}
                                    {isExpanded && (
                                        <div className="ml-8 space-y-1">
                                            {isLoadingPdfs ? (
                                                <div className="flex items-center gap-2 p-2 text-gray-400">
                                                    <Loader2 className="h-4 w-4 animate-spin" />
                                                    <span className="text-xs">{t('common.loading')}</span>
                                                </div>
                                            ) : pdfs.length === 0 ? (
                                                <div className="p-2 text-xs text-gray-400">{t('batch.empty.pdf')}</div>
                                            ) : (
                                                pdfs.map(pdf => {
                                                    const isSelected = selectedPdfs.has(pdf.path)
                                                    const attachmentCount = pdf.attachment_count || 0
                                                    return (
                                                        <div
                                                            key={pdf.path}
                                                            onClick={() => !isRunning && togglePdfSelection(pdf.path)}
                                                            className={cn(
                                                                "flex items-center gap-2 p-2 rounded-lg transition-all cursor-pointer",
                                                                isSelected ? "bg-blue-50 border border-blue-200" : "hover:bg-gray-50",
                                                                isRunning && "opacity-50 cursor-not-allowed"
                                                            )}
                                                        >
                                                            {isSelected ? (
                                                                <CheckSquare className="h-4 w-4 text-blue-600 shrink-0" />
                                                            ) : (
                                                                <SquareIcon className="h-4 w-4 text-gray-300 shrink-0" />
                                                            )}
                                                            <FileText className="h-4 w-4 text-red-400 shrink-0" />
                                                            <span className="text-sm text-gray-700 truncate flex-1">{pdf.name}</span>
                                                            {/* 附件数量徽标 */}
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
                                                })

                                            )}
                                        </div>
                                    )}
                                </div>
                            )
                        })
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
                    </div>
                </div>

                {/* 任务卡片列表 */}
                <div className="flex-1 overflow-y-auto p-6">
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
