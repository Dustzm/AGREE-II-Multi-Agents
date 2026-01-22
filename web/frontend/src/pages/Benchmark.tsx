import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Upload, Download, Play, Trash2, FileSpreadsheet, FolderOpen, Check, AlertCircle, Loader2, BarChart2 } from 'lucide-react'
import { api, HumanFileInfo, BatchInfo, BenchmarkResultInfo, BenchmarkSummary } from '@/services/api'
import { BenchmarkVisualization } from '@/components/BenchmarkVisualization'

export function Benchmark() {
    const { t } = useTranslation()

    // State
    const [humanFiles, setHumanFiles] = useState<HumanFileInfo[]>([])
    const [selectedHumanFile, setSelectedHumanFile] = useState<string | null>(null)
    const [batches, setBatches] = useState<BatchInfo[]>([])
    const [selectedBatches, setSelectedBatches] = useState<string[]>([])
    const [results, setResults] = useState<BenchmarkResultInfo[]>([])
    const [isRunning, setIsRunning] = useState(false)
    const [runResult, setRunResult] = useState<{ summary: BenchmarkSummary; xlsxFile: string } | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [loading, setLoading] = useState(true)

    const fileInputRef = useRef<HTMLInputElement>(null)

    // 加载数据
    useEffect(() => {
        loadData()
    }, [])

    const loadData = async () => {
        setLoading(true)
        setError(null) // 清除之前的错误

        // 独立加载各项数据，单个失败不影响其他
        try {
            const humanFilesRes = await api.listHumanFiles()
            setHumanFiles(humanFilesRes.files)
        } catch (err) {
            console.warn('加载人类评分文件列表失败:', err)
            setHumanFiles([])
        }

        try {
            const batchesRes = await api.listBatches()
            setBatches(batchesRes.batches)
        } catch (err) {
            console.warn('加载 batch 列表失败:', err)
            setBatches([])
        }

        try {
            const resultsRes = await api.listBenchmarkResults()
            setResults(resultsRes.results)
        } catch (err) {
            console.warn('加载结果列表失败:', err)
            setResults([])
        }

        setLoading(false)
    }

    // 上传文件
    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]
        if (!file) return

        try {
            await api.uploadHumanFile(file)
            await loadData()
        } catch (err) {
            setError(err instanceof Error ? err.message : '上传失败')
        }

        if (fileInputRef.current) {
            fileInputRef.current.value = ''
        }
    }

    // 删除文件
    const handleDeleteFile = async (filename: string) => {
        try {
            await api.deleteHumanFile(filename)
            if (selectedHumanFile === filename) {
                setSelectedHumanFile(null)
            }
            await loadData()
        } catch (err) {
            setError(err instanceof Error ? err.message : '删除失败')
        }
    }

    // 选择 batch
    const handleBatchSelect = (batchName: string) => {
        setSelectedBatches(prev => {
            if (prev.includes(batchName)) {
                return prev.filter(b => b !== batchName)
            }
            if (prev.length >= 2) {
                // 已选2个，替换最早选的
                return [prev[1], batchName]
            }
            return [...prev, batchName]
        })
    }

    // 执行 Benchmark
    const handleRun = async () => {
        if (!selectedHumanFile || selectedBatches.length !== 2) {
            setError('请选择人类评分文件和恰好两个 Batch 目录')
            return
        }

        setIsRunning(true)
        setError(null)
        setRunResult(null)

        try {
            const response = await api.runBenchmark(selectedHumanFile, selectedBatches)
            if (response.status === 'success' && response.summary) {
                setRunResult({
                    summary: response.summary,
                    xlsxFile: response.xlsx_file!,
                })
                await loadData() // 刷新结果列表
            } else {
                setError(response.error || response.message)
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : '执行失败')
        } finally {
            setIsRunning(false)
        }
    }

    // 下载结果
    const handleDownload = (filename: string) => {
        window.open(api.getBenchmarkDownloadUrl(filename), '_blank')
    }

    // 格式化百分比
    const formatPercent = (value: number) => `${(value * 100).toFixed(1)}%`

    if (loading) {
        return (
            <div className="flex-1 flex items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
            </div>
        )
    }

    return (
        <div className="flex-1 p-6 overflow-auto bg-gradient-to-br from-slate-50 to-blue-50/30">
            {/* Header */}
            <div className="mb-6">
                <div className="flex items-center gap-3 mb-2">
                    <div className="p-2 rounded-xl bg-gradient-to-br from-violet-500 to-purple-600 shadow-lg shadow-purple-500/20">
                        <BarChart2 className="w-6 h-6 text-white" />
                    </div>
                    <div>
                        <h1 className="text-2xl font-bold text-gray-800">{t('benchmark.title')}</h1>
                        <p className="text-sm text-gray-500">{t('benchmark.description')}</p>
                    </div>
                </div>
            </div>

            {/* Error Alert */}
            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-xl flex items-center gap-3">
                    <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
                    <span className="text-red-700">{error}</span>
                    <button
                        onClick={() => setError(null)}
                        className="ml-auto text-red-500 hover:text-red-700"
                    >
                        ×
                    </button>
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* 左侧：人类评分文件 */}
                <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="font-semibold text-gray-800 flex items-center gap-2">
                            <FileSpreadsheet className="w-5 h-5 text-green-500" />
                            {t('benchmark.humanFiles.title')}
                        </h2>
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept=".xlsx"
                            onChange={handleFileUpload}
                            className="hidden"
                        />
                        <button
                            onClick={() => fileInputRef.current?.click()}
                            className="p-2 rounded-lg bg-green-50 text-green-600 hover:bg-green-100 transition-colors"
                        >
                            <Upload className="w-4 h-4" />
                        </button>
                    </div>

                    <div className="space-y-2 max-h-64 overflow-y-auto">
                        {humanFiles.length === 0 ? (
                            <div className="text-center py-8 text-gray-400">
                                <FileSpreadsheet className="w-10 h-10 mx-auto mb-2 opacity-50" />
                                <p className="text-sm">{t('benchmark.humanFiles.empty')}</p>
                            </div>
                        ) : (
                            humanFiles.map(file => (
                                <div
                                    key={file.name}
                                    onClick={() => setSelectedHumanFile(file.name)}
                                    className={`p-3 rounded-xl cursor-pointer transition-all flex items-center gap-3 group ${selectedHumanFile === file.name
                                        ? 'bg-green-50 border-2 border-green-300'
                                        : 'bg-gray-50 hover:bg-gray-100 border-2 border-transparent'
                                        }`}
                                >
                                    {selectedHumanFile === file.name && (
                                        <Check className="w-4 h-4 text-green-500 flex-shrink-0" />
                                    )}
                                    <div className="flex-1 min-w-0">
                                        <p className="text-sm font-medium text-gray-700 truncate">{file.name}</p>
                                        <p className="text-xs text-gray-400">{file.modified}</p>
                                    </div>
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation()
                                            handleDeleteFile(file.name)
                                        }}
                                        className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-100 text-gray-400 hover:text-red-500 transition-all"
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </button>
                                </div>
                            ))
                        )}
                    </div>
                </div>

                {/* 中间：Batch 选择 */}
                <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="font-semibold text-gray-800 flex items-center gap-2">
                            <FolderOpen className="w-5 h-5 text-blue-500" />
                            {t('benchmark.batches.title')}
                        </h2>
                        <span className="text-xs text-gray-400 bg-gray-100 px-2 py-1 rounded-full">
                            {t('benchmark.batches.selectHint', { count: selectedBatches.length })}
                        </span>
                    </div>

                    <div className="space-y-2 max-h-64 overflow-y-auto">
                        {batches.length === 0 ? (
                            <div className="text-center py-8 text-gray-400">
                                <FolderOpen className="w-10 h-10 mx-auto mb-2 opacity-50" />
                                <p className="text-sm">{t('benchmark.batches.empty')}</p>
                            </div>
                        ) : (
                            batches.map(batch => (
                                <div
                                    key={batch.name}
                                    onClick={() => handleBatchSelect(batch.name)}
                                    className={`p-3 rounded-xl cursor-pointer transition-all flex items-center gap-3 ${selectedBatches.includes(batch.name)
                                        ? 'bg-blue-50 border-2 border-blue-300'
                                        : 'bg-gray-50 hover:bg-gray-100 border-2 border-transparent'
                                        }`}
                                >
                                    {selectedBatches.includes(batch.name) && (
                                        <div className="w-5 h-5 rounded-full bg-blue-500 text-white text-xs flex items-center justify-center flex-shrink-0">
                                            {selectedBatches.indexOf(batch.name) + 1}
                                        </div>
                                    )}
                                    <div className="flex-1 min-w-0">
                                        <p className="text-sm font-medium text-gray-700 truncate">{batch.name}</p>
                                        <p className="text-xs text-gray-400">
                                            {batch.task_count} {t('benchmark.batches.tasks')} · {batch.created}
                                        </p>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </div>

                {/* 右侧：执行与结果 */}
                <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
                    <h2 className="font-semibold text-gray-800 flex items-center gap-2 mb-4">
                        <Play className="w-5 h-5 text-purple-500" />
                        {t('benchmark.run.title')}
                    </h2>

                    {/* 执行按钮 */}
                    <button
                        onClick={handleRun}
                        disabled={isRunning || !selectedHumanFile || selectedBatches.length !== 2}
                        className={`w-full py-3 rounded-xl font-medium transition-all flex items-center justify-center gap-2 ${isRunning || !selectedHumanFile || selectedBatches.length !== 2
                            ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                            : 'bg-gradient-to-r from-violet-500 to-purple-600 text-white shadow-lg shadow-purple-500/20 hover:shadow-xl hover:shadow-purple-500/30'
                            }`}
                    >
                        {isRunning ? (
                            <>
                                <Loader2 className="w-5 h-5 animate-spin" />
                                {t('benchmark.run.running')}
                            </>
                        ) : (
                            <>
                                <Play className="w-5 h-5" />
                                {t('benchmark.run.start')}
                            </>
                        )}
                    </button>

                    {/* 执行结果摘要 */}
                    {runResult && (
                        <div className="mt-4 p-4 bg-green-50 rounded-xl border border-green-200">
                            <div className="flex items-center gap-2 mb-3">
                                <Check className="w-5 h-5 text-green-500" />
                                <span className="font-medium text-green-700">{t('benchmark.run.success')}</span>
                            </div>
                            <div className="grid grid-cols-2 gap-3 text-sm mb-3">
                                <div className="bg-white rounded-lg p-2 text-center">
                                    <p className="text-gray-500">{t('benchmark.summary.guides')}</p>
                                    <p className="text-lg font-bold text-gray-800">
                                        {runResult.summary.success_count}/{runResult.summary.total_guides}
                                    </p>
                                </div>
                                <div className="bg-white rounded-lg p-2 text-center">
                                    <p className="text-gray-500">{t('benchmark.summary.consistency')}</p>
                                    <p className="text-lg font-bold text-purple-600">
                                        {formatPercent(runResult.summary.overall_consistency)}
                                    </p>
                                </div>
                            </div>
                            <button
                                onClick={() => handleDownload(runResult.xlsxFile)}
                                className="w-full py-2 rounded-lg bg-white border border-green-300 text-green-600 hover:bg-green-50 transition-colors flex items-center justify-center gap-2"
                            >
                                <Download className="w-4 h-4" />
                                {t('benchmark.run.download')}
                            </button>
                        </div>
                    )}

                    {/* 历史结果 */}
                    <div className="mt-4">
                        <h3 className="text-sm font-medium text-gray-500 mb-2">{t('benchmark.results.title')}</h3>
                        <div className="space-y-2 max-h-40 overflow-y-auto">
                            {results.length === 0 ? (
                                <p className="text-sm text-gray-400 text-center py-4">{t('benchmark.results.empty')}</p>
                            ) : (
                                results.slice(0, 5).map(result => (
                                    <div
                                        key={result.name}
                                        className="flex items-center justify-between p-2 rounded-lg bg-gray-50 hover:bg-gray-100 transition-colors"
                                    >
                                        <div className="flex-1 min-w-0">
                                            <p className="text-sm truncate text-gray-700">{result.name}</p>
                                            <p className="text-xs text-gray-400">{result.created}</p>
                                        </div>
                                        <button
                                            onClick={() => handleDownload(result.name)}
                                            className="p-2 rounded-lg hover:bg-gray-200 text-gray-500 hover:text-blue-600 transition-colors"
                                        >
                                            <Download className="w-4 h-4" />
                                        </button>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {/* 可视化区域 - 全宽展示 */}
            {runResult && (
                <BenchmarkVisualization summary={runResult.summary} />
            )}
        </div>
    )
}
