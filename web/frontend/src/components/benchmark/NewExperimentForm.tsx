import { notifyError, notifySuccess } from '@/i18n/notify'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, FileSpreadsheet, FolderOpen, Info, Loader2, Play, Upload } from 'lucide-react'
import { HumanFileInfo, BatchInfo } from '@/services/api'

interface NewExperimentFormProps {
  humanFiles: HumanFileInfo[]
  batches: BatchInfo[]
  isRunning: boolean
  onRun: (humanFile: string, batches: string[], threshold: number) => Promise<void>
}

export function NewExperimentForm({
  humanFiles,
  batches,
  isRunning,
  onRun
}: NewExperimentFormProps) {
  const { t } = useTranslation()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [selectedHumanFile, setSelectedHumanFile] = useState<string | null>(null)
  const [selectedBatches, setSelectedBatches] = useState<string[]>([])
  const [threshold, setThreshold] = useState(2)

  const selectedBatchInfos = selectedBatches
    .map((name) => batches.find((batch) => batch.name === name))
    .filter(Boolean) as BatchInfo[]

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    notifySuccess('server.status.uploaded', { filename: file.name })
  }

  const handleBatchToggle = (batchName: string) => {
    setSelectedBatches((prev) => {
      if (prev.includes(batchName)) {
        return prev.filter((batch) => batch !== batchName)
      }
      if (prev.length >= 2) {
        return [prev[1], batchName]
      }
      return [...prev, batchName]
    })
  }

  const handleRun = async () => {
    if (!selectedHumanFile || selectedBatches.length !== 2) {
      notifyError({ key: 'benchmark.form.error.selection' })
      return
    }

    try {
      await onRun(selectedHumanFile, selectedBatches, threshold)
    } catch (error) {
      notifyError(error, 'benchmark.form.error.run')
    }
  }

  const canRun = Boolean(selectedHumanFile) && selectedBatches.length === 2 && !isRunning

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-50">
      <div className="border-b border-slate-200 bg-white/90 px-6 py-5 backdrop-blur">
        <div className="space-y-2">
          <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
            {t('benchmark.form.title')}
          </h2>
          <p className="max-w-3xl text-sm leading-6 text-slate-500">
            {t('benchmark.form.description')}
          </p>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden px-6 py-6">
        <div className="grid h-full min-h-0 gap-6 md:grid-rows-2 xl:grid-cols-2 xl:grid-rows-1">
          <section className="flex min-h-0 flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white">
            <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
              <div>
                <h3 className="flex items-center gap-2 text-base font-semibold text-slate-800">
                  <FileSpreadsheet className="h-4 w-4 text-emerald-600" />
                  {t('benchmark.form.humanFile.title')}
                </h3>
                <p className="mt-1 text-sm text-slate-500">
                  {t('benchmark.form.humanFile.helper')}
                </p>
              </div>
              <div>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".xlsx"
                  onChange={handleFileUpload}
                  className="hidden"
                />
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-100"
                >
                  <Upload className="h-3.5 w-3.5" />
                  {t('benchmark.form.humanFile.upload')}
                </button>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              {humanFiles.length === 0 ? (
                <div className="flex h-full min-h-[280px] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-6 text-center text-slate-400">
                  <FileSpreadsheet className="mb-3 h-10 w-10 opacity-50" />
                  <p className="text-sm">{t('benchmark.form.humanFile.empty')}</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {humanFiles.map((file) => {
                    const isSelected = selectedHumanFile === file.name

                    return (
                      <button
                        key={file.name}
                        type="button"
                        onClick={() => setSelectedHumanFile(file.name)}
                        className={`
                          flex w-full items-start gap-3 rounded-2xl border px-4 py-4 text-left transition-all
                          ${isSelected
                            ? 'border-emerald-300 bg-emerald-50 shadow-sm shadow-emerald-100/70'
                            : 'border-slate-200 bg-slate-50 hover:border-slate-300 hover:bg-slate-100'
                          }
                        `}
                      >
                        <div className={`
                          mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full border
                          ${isSelected
                            ? 'border-emerald-500 bg-emerald-500 text-white'
                            : 'border-slate-300 bg-white text-transparent'
                          }
                        `}>
                          <Check className="h-3 w-3" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className={`truncate text-sm font-medium ${isSelected ? 'text-emerald-700' : 'text-slate-700'}`}>
                            {file.name}
                          </p>
                          <p className="mt-1 text-xs text-slate-400">{file.modified}</p>
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </section>

          <section className="flex min-h-0 flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white">
            <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
              <div>
                <h3 className="flex items-center gap-2 text-base font-semibold text-slate-800">
                  <FolderOpen className="h-4 w-4 text-blue-600" />
                  {t('benchmark.form.batch.title')}
                </h3>
                <p className="mt-1 text-sm text-slate-500">
                  {t('benchmark.form.batch.helper')}
                </p>
              </div>
              <span className={`
                rounded-full px-2.5 py-1 text-xs font-medium
                ${selectedBatches.length === 2
                  ? 'bg-emerald-100 text-emerald-700'
                  : 'bg-amber-100 text-amber-700'
                }
              `}>
                {t('benchmark.form.batch.selected', { count: selectedBatches.length })}
              </span>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              {batches.length === 0 ? (
                <div className="flex h-full min-h-[280px] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-6 text-center text-slate-400">
                  <FolderOpen className="mb-3 h-10 w-10 opacity-50" />
                  <p className="text-sm">{t('benchmark.form.batch.empty')}</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {batches.map((batch) => {
                    const isSelected = selectedBatches.includes(batch.name)
                    const selectionOrder = selectedBatches.indexOf(batch.name) + 1

                    return (
                      <button
                        key={batch.name}
                        type="button"
                        onClick={() => handleBatchToggle(batch.name)}
                        className={`
                          flex w-full items-start gap-3 rounded-2xl border px-4 py-4 text-left transition-all
                          ${isSelected
                            ? 'border-blue-300 bg-blue-50 shadow-sm shadow-blue-100/70'
                            : 'border-slate-200 bg-slate-50 hover:border-slate-300 hover:bg-slate-100'
                          }
                        `}
                      >
                        <div className={`
                          mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-[11px] font-semibold
                          ${isSelected
                            ? 'bg-blue-600 text-white'
                            : 'border border-slate-300 bg-white text-slate-400'
                          }
                        `}>
                          {isSelected ? selectionOrder : '+'}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className={`truncate text-sm font-medium ${isSelected ? 'text-blue-700' : 'text-slate-700'}`}>
                            {batch.name}
                          </p>
                          <p className="mt-1 text-xs text-slate-400">
                            {batch.task_count} {t('benchmark.form.batch.tasks')} · {batch.created}
                          </p>
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>

      <div className="border-t border-slate-200 bg-white/95 px-6 py-4 backdrop-blur">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0 flex-1 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold text-slate-700">
                {t('benchmark.form.threshold.title')}
              </h3>
              <div className="group relative">
                <button
                  type="button"
                  className="flex h-5 w-5 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/30"
                  aria-label={t('benchmark.form.threshold.tooltipAriaLabel')}
                >
                  <Info className="h-3.5 w-3.5" />
                </button>
                <div className="invisible pointer-events-none absolute left-0 top-full z-20 mt-2 w-72 translate-y-1 rounded-2xl border border-slate-200 bg-white p-3 text-left opacity-0 shadow-xl shadow-slate-200/70 transition-all duration-200 group-hover:visible group-hover:translate-y-0 group-hover:opacity-100 group-focus-within:visible group-focus-within:translate-y-0 group-focus-within:opacity-100">
                  <p className="text-xs font-semibold text-slate-700">
                    {t('benchmark.form.threshold.tooltipTitle')}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    {t('benchmark.form.threshold.tooltipDescription')}
                  </p>
                </div>
              </div>
              <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">
                {t('benchmark.form.threshold.value', { value: threshold })}
              </span>
            </div>

            <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
              <span className="text-xs font-medium text-slate-400">1</span>
              <input
                type="range"
                min="1"
                max="5"
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
                className="h-2 flex-1 cursor-pointer appearance-none rounded-full bg-slate-200 accent-blue-600"
              />
              <span className="text-xs font-medium text-slate-400">5</span>
            </div>
          </div>

          <div className="w-full rounded-2xl border border-slate-200 bg-white p-4 shadow-sm lg:w-[360px]">
            <div className="mb-3 space-y-1">
              <p className="text-sm font-semibold text-slate-700">
                {t('benchmark.form.startTitle')}
              </p>
              <p className="text-xs leading-5 text-slate-500">
                {t('benchmark.form.startHint')}
              </p>
            </div>

            <div className="mb-4 flex flex-wrap gap-2">
              {selectedBatchInfos.length > 0 ? (
                selectedBatchInfos.map((batch, index) => (
                  <span
                    key={batch.name}
                    className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700"
                  >
                    <span className="flex h-4 w-4 items-center justify-center rounded-full bg-blue-600 text-[10px] text-white">
                      {index + 1}
                    </span>
                    {batch.name}
                  </span>
                ))
              ) : (
                <span className="text-xs text-slate-400">
                  {t('benchmark.form.summary.batchHint')}
                </span>
              )}
            </div>

            <button
              type="button"
              onClick={handleRun}
              disabled={!canRun}
              className={`
                inline-flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-medium transition-all
                ${canRun
                  ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20 hover:bg-blue-700'
                  : 'cursor-not-allowed bg-slate-100 text-slate-400'
                }
              `}
            >
              {isRunning ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t('benchmark.form.running')}
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" />
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
