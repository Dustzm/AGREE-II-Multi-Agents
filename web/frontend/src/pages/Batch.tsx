import { formatDate } from '@/i18n/format'
import { MessageView } from '@/components/MessageView'
import { asMessage, renderMessage, uploadMessage, type UiMessage } from '@/i18n/messages'
/**
 * Batch - 批量任务运行页面
 */
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertCircle, CheckCircle2, CheckSquare, ChevronDown, ChevronRight, Clock, FileText, Folder, FolderOpen, Loader2, Play, RefreshCw, Square, Square as SquareIcon, XCircle } from 'lucide-react'

import { Toast, type ToastType } from '@/components/Toast'
import { Button } from '@/components/ui/button'
import { api } from '@/services/api'
import {
    batchApi,
    createBatchWebSocket,
    type BatchDirectoryNode,
    type BatchStatus,
    type BatchTask,
    type BatchWebSocketHandle,
    type PdfInfo,
    type ResumeLatestResponse,
} from '@/services/batch'
import { cn } from '@/utils/cn'

type TaskStatusType = BatchTask['status']
type JobStatusType = BatchStatus['status']

interface ToastMessage {
    id: number
    type: ToastType
    message: UiMessage
}

const statusConfig: Record<TaskStatusType, { icon: typeof Clock; color: string; bg: string; labelKey: string }> = {
    pending: { icon: Clock, color: 'text-gray-500', bg: 'bg-gray-100', labelKey: 'batch.taskStatus.pending' },
    running: { icon: Loader2, color: 'text-blue-600', bg: 'bg-blue-50', labelKey: 'batch.taskStatus.running' },
    done: { icon: CheckCircle2, color: 'text-green-600', bg: 'bg-green-50', labelKey: 'batch.taskStatus.done' },
    error: { icon: XCircle, color: 'text-red-600', bg: 'bg-red-50', labelKey: 'batch.taskStatus.error' },
    cancelled: { icon: AlertCircle, color: 'text-orange-500', bg: 'bg-orange-50', labelKey: 'batch.taskStatus.cancelled' },
    stopped: { icon: AlertCircle, color: 'text-orange-500', bg: 'bg-orange-50', labelKey: 'batch.taskStatus.stopped' },
}

function isJobActive(status: JobStatusType | null | undefined): boolean {
    return status === 'queued' || status === 'running' || status === 'stopping'
}

function collectDirectoryPaths(node: BatchDirectoryNode): string[] {
    return node.children.flatMap((child) => [child.path, ...collectDirectoryPaths(child)])
}

function collectDescendantPdfs(node: BatchDirectoryNode): PdfInfo[] {
    return [...node.pdfs, ...node.children.flatMap(collectDescendantPdfs)]
}

function getJobSummary(status: BatchStatus | null, t: ReturnType<typeof useTranslation>['t']): string {
    if (!status) {
        return t('batch.status.waiting')
    }
    if (status.status === 'stopping') {
        return t('batch.status.stopping')
    }
    if (status.status === 'queued' || status.status === 'running') {
        if (status.experiment_rounds > 1) {
            return `${t('batch.status.running')} - ${t('batch.status.round', {
                current: Math.max(status.current_round || 1, 1),
                total: status.experiment_rounds,
            })}`
        }
        return t('batch.status.running')
    }
    if (status.status === 'interrupted') {
        return t('batch.status.interrupted')
    }
    if (status.status === 'stopped') {
        return t('batch.status.stopped')
    }
    if (status.status === 'failed') {
        return status.tasks.some((task) => task.report_status === 'partial')
            ? t('batch.status.partial')
            : t('batch.status.failed')
    }
    if (status.total > 0) {
        return t('batch.status.completed')
    }
    return t('batch.status.waiting')
}

export function Batch() {
    const { t, i18n } = useTranslation()
    const [directoryTree, setDirectoryTree] = useState<BatchDirectoryNode | null>(null)
    const [loadingFolders, setLoadingFolders] = useState(true)
    const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())
    const [selectedPdfs, setSelectedPdfs] = useState<Set<string>>(new Set())
    const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null)
    const [resumeCandidate, setResumeCandidate] = useState<ResumeLatestResponse | null>(null)
    const [concurrency, setConcurrency] = useState(2)
    const [resumeConcurrency, setResumeConcurrency] = useState(2)
    const [experimentRounds, setExperimentRounds] = useState(1)
    const [isResuming, setIsResuming] = useState(false)
    const [isUploadingFolder, setIsUploadingFolder] = useState(false)
    const [wsConnected, setWsConnected] = useState(false)
    const [wsReconnectAttempts, setWsReconnectAttempts] = useState(0)
    const [toasts, setToasts] = useState<ToastMessage[]>([])
    const [activeJobId, setActiveJobId] = useState<string | null>(null)

    const toastIdRef = useRef(0)
    const wsHandleRef = useRef<BatchWebSocketHandle | null>(null)
    const reconnectRef = useRef<(() => void) | null>(null)
    const folderInputRef = useRef<HTMLInputElement>(null)

    const isBusy = batchStatus ? isJobActive(batchStatus.status) : false
    const isStopping = batchStatus?.status === 'stopping'
    const resumableStatus = resumeCandidate?.status ?? null
    const resumableRound = resumeCandidate?.resume_round ?? resumableStatus?.current_round ?? 0
    const resumableIncompleteCount = resumeCandidate?.remaining_tasks ?? 0
    const directoryInputProps: Record<string, string> = { webkitdirectory: '', directory: '' }

    const showToast = useCallback((message: string | UiMessage | Error, type: ToastType = 'info') => {
        const id = toastIdRef.current++
        setToasts((prev) => [...prev, { id, type, message: asMessage(message) }])
    }, [])

    const formatSize = useCallback((bytes: number) => {
        if (bytes < 1024) return `${bytes.toLocaleString(i18n.language)} B`
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toLocaleString(i18n.language, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} KB`
        return `${(bytes / (1024 * 1024)).toLocaleString(i18n.language, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`
    }, [i18n.language])

    const getStatusMessage = useCallback((task: BatchTask) => {
        if (!task.current_step && !task.step_i18n) return null
        return renderMessage(task.step_i18n ? { i18n: task.step_i18n } : asMessage(task.current_step, 'batch.status.waiting'), t, 'batch.status.waiting')
    }, [t])

    const loadFolders = useCallback(async () => {
        setLoadingFolders(true)
        try {
            const data = await batchApi.listFolders()
            setDirectoryTree(data.tree)
            setExpandedFolders(new Set(collectDirectoryPaths(data.tree)))
        } catch (error) {
            console.error('加载文件夹失败:', error)
        } finally {
            setLoadingFolders(false)
        }
    }, [])

    const loadResumeCandidate = useCallback(async (hasActiveJob?: boolean) => {
        if (hasActiveJob) {
            setResumeCandidate(null)
            return
        }
        try {
            const candidate = await batchApi.getLatestResumable()
            setResumeCandidate(candidate.available ? candidate : null)
            if (candidate.available && candidate.status?.concurrency) {
                setResumeConcurrency(candidate.status.concurrency)
            }
        } catch (error) {
            console.error('加载可继续任务失败:', error)
            setResumeCandidate(null)
        }
    }, [])

    const establishWebSocket = useCallback((jobId: string) => {
        wsHandleRef.current?.disconnect()
        const handle = createBatchWebSocket({
            sessionId: jobId,
            onTaskUpdate: (task) => {
                setBatchStatus((prev) => {
                    if (!prev || prev.job_id !== jobId) return prev
                    const existingIndex = prev.tasks.findIndex((entry) => entry.id === task.id)
                    const nextTasks = [...prev.tasks]
                    if (existingIndex >= 0) {
                        nextTasks[existingIndex] = task
                    } else {
                        nextTasks.push(task)
                    }
                    const counts = { pending: 0, running: 0, done: 0, error: 0, stopped: 0 }
                    nextTasks.forEach((entry) => {
                        if (entry.status === 'pending') counts.pending += 1
                        if (entry.status === 'running') counts.running += 1
                        if (entry.status === 'done') counts.done += 1
                        if (entry.status === 'error') counts.error += 1
                        if (entry.status === 'stopped' || entry.status === 'cancelled') counts.stopped += 1
                    })
                    return {
                        ...prev,
                        tasks: nextTasks,
                        pending: counts.pending,
                        running: counts.running,
                        done: counts.done,
                        error: counts.error,
                        stopped: counts.stopped,
                        cancelled: counts.stopped,
                    }
                })
            },
            onComplete: (status) => {
                if (status.job_id !== jobId) {
                    return
                }
                setBatchStatus(status)
                setActiveJobId(status.job_id)
                if (!isJobActive(status.status)) {
                    void loadResumeCandidate(false)
                }
            },
            onError: (error) => {
                console.error('批量 WebSocket 错误:', error)
                if (jobId) {
                    void batchApi.getJobStatus(jobId).then((status) => {
                        setBatchStatus(status)
                        setActiveJobId(status.job_id)
                    }).catch(console.error)
                }
            },
            onConnectionChange: (connected, reconnectAttempts) => {
                setWsConnected(connected)
                setWsReconnectAttempts(reconnectAttempts)
            },
        })
        wsHandleRef.current = handle
        reconnectRef.current = handle.reconnect
    }, [loadResumeCandidate])

    const loadStatus = useCallback(async () => {
        try {
            const status = await batchApi.getStatus()
            setBatchStatus(status)
            setActiveJobId(status.job_id)
            if (status.job_id && isJobActive(status.status) && !wsHandleRef.current) {
                establishWebSocket(status.job_id)
            }
            await loadResumeCandidate(isJobActive(status.status))
        } catch (error) {
            console.error('加载状态失败:', error)
        }
    }, [establishWebSocket, loadResumeCandidate])

    const handleFolderSelect = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
        const files = event.target.files
        if (!files || files.length === 0) return
        setIsUploadingFolder(true)
        try {
            const result = await api.uploadFolder(Array.from(files))
            if (result.failed_count > 0) {
                showToast(uploadMessage('batch.upload.result', result), 'error')
            } else {
                showToast({ key: 'batch.upload.success', params: { count: result.success_count } }, 'success')
            }
            await loadFolders()
        } catch (error) {
            console.error('文件夹上传失败:', error)
            showToast(asMessage(error, 'batch.error.folderUploadFailed'), 'error')
        } finally {
            setIsUploadingFolder(false)
            if (folderInputRef.current) {
                folderInputRef.current.value = ''
            }
        }
    }, [loadFolders, showToast])

    const handleStart = useCallback(async () => {
        if (selectedPdfs.size === 0) return
        try {
            const status = await batchApi.startSelected(Array.from(selectedPdfs), concurrency, experimentRounds)
            setBatchStatus(status)
            setActiveJobId(status.job_id)
            setResumeCandidate(null)
            if (status.job_id) {
                establishWebSocket(status.job_id)
            }
        } catch (error) {
            showToast(asMessage(error, 'batch.error.startFailed'), 'error')
        }
    }, [selectedPdfs, concurrency, experimentRounds, establishWebSocket, showToast])

    const handleResumeLatest = useCallback(async () => {
        setIsResuming(true)
        try {
            const status = await batchApi.resumeLatest(resumeConcurrency)
            setBatchStatus(status)
            setActiveJobId(status.job_id)
            setResumeCandidate(null)
            if (status.job_id) {
                establishWebSocket(status.job_id)
            }
            showToast({ key: 'batch.resume.success' }, 'success')
        } catch (error) {
            showToast(asMessage(error, 'batch.resume.failed'), 'error')
        } finally {
            setIsResuming(false)
            await loadResumeCandidate(false)
        }
    }, [establishWebSocket, loadResumeCandidate, resumeConcurrency, showToast])

    const handleStop = useCallback(async () => {
        if (!batchStatus?.job_id || !isJobActive(batchStatus.status)) {
            showToast({ key: 'batch.error.noTask' }, 'warning')
            return
        }
        try {
            const status = await batchApi.stop(batchStatus.job_id)
            setBatchStatus(status)
            showToast({ key: 'batch.success.stopped' }, 'success')
        } catch (error) {
            showToast(asMessage(error, 'batch.error.stopFailed'), 'error')
        }
    }, [batchStatus, showToast])

    const toggleFolder = useCallback((folderPath: string) => {
        setExpandedFolders((prev) => {
            const next = new Set(prev)
            if (next.has(folderPath)) {
                next.delete(folderPath)
            } else {
                next.add(folderPath)
            }
            return next
        })
    }, [])

    const togglePdfSelection = useCallback((pdfPath: string) => {
        setSelectedPdfs((prev) => {
            const next = new Set(prev)
            if (next.has(pdfPath)) {
                next.delete(pdfPath)
            } else {
                next.add(pdfPath)
            }
            return next
        })
    }, [])

    const toggleFolderSelection = useCallback((node: BatchDirectoryNode) => {
        const pdfs = collectDescendantPdfs(node)
        const allSelected = pdfs.every((pdf) => selectedPdfs.has(pdf.path))
        setSelectedPdfs((prev) => {
            const next = new Set(prev)
            pdfs.forEach((pdf) => {
                if (allSelected) {
                    next.delete(pdf.path)
                } else {
                    next.add(pdf.path)
                }
            })
            return next
        })
    }, [selectedPdfs])

    const getFolderCheckState = useCallback((node: BatchDirectoryNode): 'none' | 'some' | 'all' => {
        const pdfs = collectDescendantPdfs(node)
        if (pdfs.length === 0) return 'none'
        const selectedCount = pdfs.filter((pdf) => selectedPdfs.has(pdf.path)).length
        if (selectedCount === 0) return 'none'
        if (selectedCount === pdfs.length) return 'all'
        return 'some'
    }, [selectedPdfs])

    useEffect(() => {
        loadFolders()
        loadStatus()
        return () => {
            wsHandleRef.current?.disconnect()
        }
    }, [loadFolders, loadStatus])

    useEffect(() => {
        if (!activeJobId || !batchStatus || !isJobActive(batchStatus.status)) {
            return
        }
        const timer = window.setInterval(() => {
            batchApi.getJobStatus(activeJobId)
                .then((status) => {
                    setBatchStatus(status)
                    setActiveJobId(status.job_id)
                    if (!isJobActive(status.status)) {
                        void loadResumeCandidate(false)
                    }
                })
                .catch((error) => {
                    console.error('轮询批量任务状态失败:', error)
                })
        }, 3000)
        return () => {
            window.clearInterval(timer)
        }
    }, [activeJobId, batchStatus, loadResumeCandidate])

    useEffect(() => {
        const handleVisibilityChange = () => {
            if (
                document.visibilityState === 'visible' &&
                activeJobId &&
                batchStatus &&
                isJobActive(batchStatus.status) &&
                !wsConnected &&
                reconnectRef.current
            ) {
                reconnectRef.current()
            }
        }
        document.addEventListener('visibilitychange', handleVisibilityChange)
        return () => {
            document.removeEventListener('visibilitychange', handleVisibilityChange)
        }
    }, [activeJobId, batchStatus, wsConnected])

    const statusSummary = useMemo(() => getJobSummary(batchStatus, t), [batchStatus, t])

    const renderPdfRow = (pdf: PdfInfo, depth: number) => {
        const isSelected = selectedPdfs.has(pdf.path)
        return (
            <div
                key={pdf.path}
                onClick={() => !isBusy && togglePdfSelection(pdf.path)}
                style={{ marginLeft: `${depth * 14}px` }}
                className={cn(
                    'flex items-center gap-2 rounded-lg p-2 transition-all cursor-pointer',
                    isSelected ? 'bg-blue-50 border border-blue-200' : 'hover:bg-gray-50',
                    isBusy && 'opacity-50 cursor-not-allowed',
                )}
            >
                {isSelected ? (
                    <CheckSquare className="h-4 w-4 shrink-0 text-blue-600" />
                ) : (
                    <SquareIcon className="h-4 w-4 shrink-0 text-gray-300" />
                )}
                <FileText className="h-4 w-4 shrink-0 text-red-400" />
                <span className="flex-1 truncate text-sm text-gray-700">{pdf.name}</span>
                {(pdf.attachment_count || 0) > 0 && (
                    <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                        +{pdf.attachment_count}
                    </span>
                )}
                <span className="shrink-0 text-xs text-gray-400">{formatSize(pdf.size)}</span>
            </div>
        )
    }

    const renderDirectoryNode = (node: BatchDirectoryNode, depth = 0): ReactNode => {
        const isExpanded = expandedFolders.has(node.path)
        const checkState = getFolderCheckState(node)
        return (
            <div key={node.path} className="space-y-1">
                <div
                    style={{ marginLeft: `${depth * 14}px` }}
                    className={cn(
                        'flex items-center gap-2 rounded-lg p-2 transition-all hover:bg-gray-100 cursor-pointer',
                        isBusy && 'opacity-50 cursor-not-allowed',
                    )}
                >
                    <div onClick={() => !isBusy && toggleFolder(node.path)} className="p-1">
                        {isExpanded ? (
                            <ChevronDown className="h-4 w-4 text-gray-400" />
                        ) : (
                            <ChevronRight className="h-4 w-4 text-gray-400" />
                        )}
                    </div>
                    <div onClick={() => !isBusy && toggleFolderSelection(node)} className="p-1">
                        {checkState === 'all' ? (
                            <CheckSquare className="h-4 w-4 text-blue-600" />
                        ) : checkState === 'some' ? (
                            <div className="flex h-4 w-4 items-center justify-center rounded border-2 border-blue-600">
                                <div className="h-0.5 w-2 bg-blue-600" />
                            </div>
                        ) : (
                            <SquareIcon className="h-4 w-4 text-gray-300" />
                        )}
                    </div>
                    <Folder className="h-4 w-4 text-amber-500" />
                    <span className="flex-1 truncate text-sm font-medium text-gray-700">{node.name}</span>
                    <span className="text-xs text-gray-400">{node.pdf_count}</span>
                </div>
                {isExpanded && (
                    <div className="space-y-1">
                        {node.children.map((child) => renderDirectoryNode(child, depth + 1))}
                        {node.pdfs.length === 0 && node.children.length === 0 ? (
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
        <div className="flex h-full bg-[#f5f5f7]">
            <div className="flex w-96 flex-col border-r border-black/6 bg-white/92 shadow-[4px_0_24px_-12px_rgba(0,0,0,0.05)]">
                <div className="border-b border-black/6 p-6">
                    <div className="flex items-center justify-between">
                        <div>
                            <div className="text-[0.68rem] uppercase tracking-[0.22em] text-black/34">{t('batch.select.eyebrow')}</div>
                            <h2 className="mt-2 text-lg font-semibold tracking-[-0.03em] text-[#1d1d1f]">{t('batch.select.title')}</h2>
                            <p className="mt-1 text-xs text-black/48">{t('batch.select.count', { count: selectedPdfs.size })}</p>
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
                                disabled={isBusy || isUploadingFolder}
                                className="h-9 w-9 rounded-full transition-all duration-200 hover:bg-[#0071e3]/8 hover:text-[#0071e3]"
                                title={t('batch.uploadFolder')}
                            >
                                {isUploadingFolder ? (
                                    <Loader2 className="h-4 w-4 animate-spin text-gray-600" />
                                ) : (
                                    <FolderOpen className="h-4 w-4 text-gray-600 transition-colors" />
                                )}
                            </Button>
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={loadFolders}
                                disabled={loadingFolders || isUploadingFolder}
                                className="h-9 w-9 rounded-full transition-all duration-200 hover:bg-[#0071e3]/8 hover:text-[#0071e3]"
                            >
                                <RefreshCw className={cn('h-4 w-4 text-gray-600 transition-colors', loadingFolders && 'animate-spin')} />
                            </Button>
                        </div>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto p-3 space-y-1">
                    {loadingFolders ? (
                        <div className="flex items-center justify-center py-10 text-gray-500">
                            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                            <span className="text-sm">{t('common.loading')}</span>
                        </div>
                    ) : !directoryTree || (directoryTree.children.length === 0 && directoryTree.pdfs.length === 0) ? (
                        <div className="py-10 text-center text-gray-400">
                            <Folder className="mx-auto mb-2 h-10 w-10 opacity-40" />
                            <p className="text-sm">{t('batch.empty.folder')}</p>
                        </div>
                    ) : (
                        <>
                            {directoryTree.children.map((folder) => renderDirectoryNode(folder))}
                            {directoryTree.pdfs.length > 0 && (
                                <div className="mt-2 space-y-1 border-t border-gray-100 pt-2">
                                    <p className="px-2 text-xs text-gray-400">{t('batch.select.inputRoot')}</p>
                                    {directoryTree.pdfs.map((pdf) => renderPdfRow(pdf, 0))}
                                </div>
                            )}
                        </>
                    )}
                </div>

                <div className="space-y-4 border-t border-gray-100 p-4">
                    <div className="flex items-center gap-6">
                        <div className="flex items-center gap-3">
                            <label className="text-sm text-gray-600">{t('batch.concurrency.label')}</label>
                            <input
                                type="number"
                                min={1}
                                max={10}
                                value={concurrency}
                                onChange={(event) => setConcurrency(Math.max(1, Math.min(10, parseInt(event.target.value, 10) || 1)))}
                                disabled={isBusy}
                                className="w-16 rounded-lg border border-gray-200 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50"
                            />
                        </div>
                        <div className="flex items-center gap-3">
                            <label className="text-sm text-gray-600">{t('batch.rounds.label')}</label>
                            <input
                                type="number"
                                min={1}
                                max={4}
                                value={experimentRounds}
                                onChange={(event) => setExperimentRounds(Math.max(1, Math.min(4, parseInt(event.target.value, 10) || 1)))}
                                disabled={isBusy}
                                className="w-16 rounded-lg border border-gray-200 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50"
                            />
                        </div>
                    </div>

                    <div className="flex gap-2">
                        <Button
                            onClick={handleStart}
                            disabled={selectedPdfs.size === 0 || isBusy}
                            className="flex-1 rounded-full bg-[#0071e3] hover:bg-[#0066cc]"
                        >
                            <Play className="mr-2 h-4 w-4" />
                            {t('batch.button.start')} ({selectedPdfs.size})
                        </Button>
                        <Button
                            variant="outline"
                            onClick={handleStop}
                            disabled={!isBusy || isStopping}
                            className="border-red-200 text-red-600 hover:bg-red-50"
                        >
                            <Square className="mr-2 h-4 w-4" />
                            {t('batch.button.stop')}
                        </Button>
                    </div>
                </div>
            </div>

            <div className="flex flex-1 flex-col">
                <div className="border-b border-black/6 bg-white/92 p-6">
                    <div className="flex items-center justify-between">
                        <div>
                            <div className="text-[0.68rem] uppercase tracking-[0.22em] text-black/34">{t('batch.status.eyebrow')}</div>
                            <h2 className="mt-2 text-lg font-semibold tracking-[-0.03em] text-[#1d1d1f]">{t('batch.status.title')}</h2>
                            <p className="mt-1 text-xs text-black/48">{statusSummary}</p>
                        </div>
                        <div className="flex items-center gap-4">
                            {batchStatus && batchStatus.total > 0 && (
                                <div className="flex items-center gap-4 text-sm">
                                    <div className="flex items-center gap-1.5">
                                        <span className="h-2 w-2 rounded-full bg-green-500" />
                                        <span className="text-gray-600">{batchStatus.done}</span>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        <span className="h-2 w-2 rounded-full bg-blue-500" />
                                        <span className="text-gray-600">{batchStatus.running}</span>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        <span className="h-2 w-2 rounded-full bg-gray-300" />
                                        <span className="text-gray-600">{batchStatus.pending}</span>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        <span className="h-2 w-2 rounded-full bg-red-500" />
                                        <span className="text-gray-600">{batchStatus.error}</span>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        <span className="h-2 w-2 rounded-full bg-amber-400" />
                                        <span className="text-gray-600">{batchStatus.stopped || batchStatus.cancelled}</span>
                                    </div>
                                </div>
                            )}
                            {wsReconnectAttempts > 0 ? (
                                <div className="flex items-center gap-2 rounded-lg border border-yellow-200 bg-yellow-50 px-3 py-1.5">
                                    <Loader2 className="h-3 w-3 animate-spin text-yellow-600" />
                                    <span className="text-xs font-medium text-yellow-700">{t('batch.connection.reconnecting', { current: wsReconnectAttempts, max: 5 })}</span>
                                </div>
                            ) : !wsConnected && isBusy ? (
                                <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5">
                                    <span className="h-2 w-2 rounded-full bg-red-500" />
                                    <span className="text-xs font-medium text-red-600">{t('batch.connection.disconnected')}</span>
                                </div>
                            ) : null}
                        </div>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto p-6">
                    {batchStatus?.last_error && (
                        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-red-700">
                            <MessageView message={batchStatus.error_i18n
                                ? { i18n: batchStatus.error_i18n, diagnostic: batchStatus.diagnostic }
                                : asMessage(batchStatus.last_error, 'batch.status.failed')} />
                        </div>
                    )}
                    {!isBusy && resumableStatus && (
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
                                            <span>{t('batch.resume.updated', { time: formatDate(resumeCandidate.last_updated, i18n.language) })}</span>
                                        )}
                                    </div>
                                </div>
                                <div className="flex shrink-0 items-end gap-2">
                                    <div className="flex flex-col gap-1">
                                        <label className="text-xs font-medium text-amber-800">{t('batch.resume.concurrency')}</label>
                                        <input
                                            type="number"
                                            min={1}
                                            max={10}
                                            value={resumeConcurrency}
                                            onChange={(event) => setResumeConcurrency(Math.max(1, Math.min(10, parseInt(event.target.value, 10) || 1)))}
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
                        <div className="flex h-full flex-col items-center justify-center text-gray-400">
                            <Clock className="mb-3 h-12 w-12 opacity-30" />
                            <p className="text-sm">{t('batch.status.hint')}</p>
                        </div>
                    ) : (
                        <div className="grid gap-3">
                            {batchStatus.tasks.map((task) => {
                                const partialReport = task.status === 'error' && task.report_status === 'partial'
                                const config = partialReport
                                    ? { icon: AlertCircle, color: 'text-amber-700', bg: 'bg-amber-50', labelKey: 'batch.taskStatus.partial' }
                                    : statusConfig[task.status] || statusConfig.stopped
                                const StatusIcon = config.icon
                                return (
                                    <div
                                        key={task.id}
                                        className={cn(
                                            'flex items-center gap-4 rounded-xl border p-4 transition-all',
                                            config.bg,
                                            task.status === 'running' ? 'border-blue-200 shadow-sm' : 'border-transparent',
                                        )}
                                    >
                                        <div className={cn('rounded-lg p-2', config.bg)}>
                                            <StatusIcon className={cn('h-5 w-5', config.color, task.status === 'running' && 'animate-spin')} />
                                        </div>
                                        <div className="min-w-0 flex-1">
                                            <h4 className="truncate text-sm font-medium text-gray-800">{task.id}</h4>
                                            <p className={cn('mt-0.5 truncate text-xs', config.color)}>
                                                {partialReport ? t(config.labelKey) : getStatusMessage(task) || t(config.labelKey)}
                                                {task.retry_count > 0 && ` (${t('batch.status.retry', { count: task.retry_count })})`}
                                            </p>
                                        </div>
                                        {task.status === 'error' && task.error && (
                                            <div className="max-w-sm text-xs text-red-500">
                                                <MessageView message={task.error_i18n ? { i18n: task.error_i18n, diagnostic: task.diagnostic } : asMessage(task.error)} />
                                            </div>
                                        )}
                                    </div>
                                )
                            })}
                        </div>
                    )}
                </div>
            </div>

            {toasts.map((toast) => (
                <Toast
                    key={toast.id}
                    type={toast.type}
                    message={toast.message}
                    duration={toast.type === 'error' ? 8000 : 3000}
                    onClose={() => {
                        setToasts((prev) => prev.filter((entry) => entry.id !== toast.id))
                    }}
                />
            ))}
        </div>
    )
}
