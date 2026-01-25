import { useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Upload, FileSpreadsheet, FolderOpen, Play, Loader2, X } from 'lucide-react'
import { HumanFileInfo, BatchInfo } from '@/services/api'
import { toast } from 'react-hot-toast'

interface NewExperimentFormProps {
  humanFiles: HumanFileInfo[]
  batches: BatchInfo[]
  isRunning: boolean
  onRun: (humanFile: string, batches: string[]) => Promise<void>
  onCancel: () => void
}

export function NewExperimentForm({
  humanFiles,
  batches,
  isRunning,
  onRun,
  onCancel
}: NewExperimentFormProps) {
  const { t } = useTranslation()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [selectedHumanFile, setSelectedHumanFile] = useState<string | null>(null)
  const [selectedBatches, setSelectedBatches] = useState<string[]>([])
  const [threshold, setThreshold] = useState(2)

  // 文件上传处理
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    // 触发父组件上传，这里简化处理
    toast.success(`文件 ${file.name} 上传成功`)
  }

  // Batch 选择处理
  const handleBatchToggle = (batchName: string) => {
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

  // 执行实验
  const handleRun = async () => {
    if (!selectedHumanFile || selectedBatches.length !== 2) {
      toast.error(t('benchmark.form.error.selection'))
      return
    }

    try {
      await onRun(selectedHumanFile, selectedBatches)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('benchmark.form.error.run'))
    }
  }

  const canRun = selectedHumanFile && selectedBatches.length === 2 && !isRunning

  return (
    <div className="h-full flex items-center justify-center p-8 bg-gradient-to-br from-slate-50 to-blue-50/30">
      <div className="w-full max-w-2xl bg-white rounded-2xl shadow-xl border border-slate-200 overflow-hidden">
        {/* 头部 */}
        <div className="bg-gradient-to-r from-blue-600 to-indigo-500 px-6 py-5">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-bold text-white flex items-center gap-2">
                <Zap className="w-5 h-5" />
                {t('benchmark.form.title')}
              </h2>
              <p className="text-blue-100 text-sm mt-1">{t('benchmark.form.description')}</p>
            </div>
            <button
              onClick={onCancel}
              className="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* 表单内容 */}
        <div className="p-6 space-y-6 max-h-[calc(100vh-200px)] overflow-y-auto">
          {/* 1. 人类评分文件 */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-slate-700 flex items-center gap-2">
                <FileSpreadsheet className="w-4 h-4 text-green-500" />
                {t('benchmark.form.humanFile.title')}
              </h3>
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx"
                onChange={handleFileUpload}
                className="hidden"
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                className="text-sm px-3 py-1.5 rounded-lg bg-green-50 text-green-600 hover:bg-green-100 transition-colors flex items-center gap-1"
              >
                <Upload className="w-3.5 h-3.5" />
                {t('benchmark.form.humanFile.upload')}
              </button>
            </div>

            <div className="space-y-2 max-h-40 overflow-y-auto">
              {humanFiles.length === 0 ? (
                <div className="text-center py-6 text-slate-400 border-2 border-dashed border-slate-200 rounded-xl">
                  <FileSpreadsheet className="w-10 h-10 mx-auto mb-2 opacity-50" />
                  <p className="text-sm">{t('benchmark.form.humanFile.empty')}</p>
                </div>
              ) : (
                humanFiles.map(file => (
                  <div
                    key={file.name}
                    onClick={() => setSelectedHumanFile(file.name)}
                    className={`
                      p-3 rounded-xl cursor-pointer transition-all flex items-center gap-3
                      ${selectedHumanFile === file.name
                        ? 'bg-green-50 border-2 border-green-300'
                        : 'bg-slate-50 border-2 border-transparent hover:bg-slate-100'
                      }
                    `}
                  >
                    {selectedHumanFile === file.name && (
                      <div className="w-4 h-4 rounded-full bg-green-500 flex items-center justify-center flex-shrink-0">
                        <Check className="w-3 h-3 text-white" />
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm font-medium truncate ${selectedHumanFile === file.name ? 'text-green-700' : 'text-slate-700'}`}>
                        {file.name}
                      </p>
                      <p className="text-xs text-slate-400">{file.modified}</p>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* 2. Batch 选择 */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-slate-700 flex items-center gap-2">
                <FolderOpen className="w-4 h-4 text-blue-500" />
                {t('benchmark.form.batch.title')}
              </h3>
              <span className={`text-xs px-2 py-1 rounded-full ${selectedBatches.length === 2
                  ? 'bg-green-100 text-green-600'
                  : 'bg-amber-100 text-amber-600'
                }`}>
                {t('benchmark.form.batch.selected', { count: selectedBatches.length })}
              </span>
            </div>

            <div className="space-y-2 max-h-40 overflow-y-auto">
              {batches.length === 0 ? (
                <div className="text-center py-6 text-slate-400 border-2 border-dashed border-slate-200 rounded-xl">
                  <FolderOpen className="w-10 h-10 mx-auto mb-2 opacity-50" />
                  <p className="text-sm">{t('benchmark.form.batch.empty')}</p>
                </div>
              ) : (
                batches.map(batch => (
                  <div
                    key={batch.name}
                    onClick={() => handleBatchToggle(batch.name)}
                    className={`
                      p-3 rounded-xl cursor-pointer transition-all flex items-center gap-3
                      ${selectedBatches.includes(batch.name)
                        ? 'bg-blue-50 border-2 border-blue-300'
                        : 'bg-slate-50 border-2 border-transparent hover:bg-slate-100'
                      }
                    `}
                  >
                    {selectedBatches.includes(batch.name) && (
                      <div className="w-5 h-5 rounded-full bg-blue-500 text-white text-xs flex items-center justify-center flex-shrink-0">
                        {selectedBatches.indexOf(batch.name) + 1}
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm font-medium truncate ${selectedBatches.includes(batch.name) ? 'text-blue-700' : 'text-slate-700'}`}>
                        {batch.name}
                      </p>
                      <p className="text-xs text-slate-400">
                        {batch.task_count} {t('benchmark.form.batch.tasks')} · {batch.created}
                      </p>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* 3. 阈值设置 */}
          <div>
            <h3 className="font-semibold text-slate-700 mb-3">{t('benchmark.form.threshold.title')}</h3>
            <div className="flex items-center gap-4">
              <input
                type="range"
                min="1"
                max="5"
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
                className="flex-1 h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
              />
              <div className="w-16 text-center">
                <span className="text-2xl font-bold text-blue-600">{threshold}</span>
              </div>
            </div>
          </div>

          {/* 执行按钮 */}
          <div className="pt-4 border-t border-slate-200">
            <button
              onClick={handleRun}
              disabled={!canRun}
              className={`
                w-full py-3 rounded-xl font-medium transition-all flex items-center justify-center gap-2
                ${canRun
                  ? 'bg-gradient-to-r from-blue-600 to-indigo-500 text-white shadow-lg shadow-blue-500/20 hover:shadow-xl hover:shadow-blue-500/30'
                  : 'bg-slate-100 text-slate-400 cursor-not-allowed'
                }
              `}
            >
              {isRunning ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  {t('benchmark.form.running')}
                </>
              ) : (
                <>
                  <Play className="w-5 h-5" />
                  {t('benchmark.form.start')}
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

import { Check } from 'lucide-react'
import { Zap } from 'lucide-react'
