import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Clock, FileText, Trash2, Check, Zap } from 'lucide-react'
import { BenchmarkResultInfo } from '@/services/api'

interface SidebarProps {
  results: BenchmarkResultInfo[]
  selectedResult: string | null
  isLoading: boolean
  onSelectResult: (filename: string) => void
  onDeleteResult: (filename: string) => void
  onClickNew: () => void
  onViewMode: 'history' | 'new'
}

export function Sidebar({
  results,
  selectedResult,
  isLoading,
  onSelectResult,
  onDeleteResult,
  onClickNew,
  onViewMode
}: SidebarProps) {
  const { t } = useTranslation()
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  const handleDeleteClick = (e: React.MouseEvent, filename: string) => {
    e.stopPropagation()
    if (deleteConfirm === filename) {
      onDeleteResult(filename)
      setDeleteConfirm(null)
    } else {
      setDeleteConfirm(filename)
      setTimeout(() => setDeleteConfirm(null), 3000)
    }
  }

  // 格式化文件名显示
  const formatDisplayName = (name: string) => {
    // bench_20260123_133352.xlsx -> 2026-01-23 13:33
    const match = name.match(/bench_(\d{8})_(\d{6})/)
    if (match) {
      const [, date, time] = match
      const formattedDate = `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`
      const formattedTime = `${time.slice(0, 2)}:${time.slice(2, 4)}`
      return `${formattedDate} ${formattedTime}`
    }
    return name
  }

  return (
    <div className="w-80 bg-slate-50 border-r border-slate-200 flex flex-col h-full">
      {/* 头部 */}
      <div className="p-4 border-b border-slate-200">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-slate-700 flex items-center gap-2">
            <FileText className="w-4 h-4 text-blue-500" />
            {t('benchmark.sidebar.title')}
          </h2>
          <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
            {results.length}
          </span>
        </div>

        {/* 新建实验按钮 */}
        <button
          onClick={onClickNew}
          className={`
            w-full py-2.5 px-4 rounded-xl font-medium transition-all flex items-center justify-center gap-2
            ${onViewMode === 'new'
              ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20'
              : 'bg-white border border-slate-200 text-slate-600 hover:border-blue-300 hover:text-blue-600'
            }
          `}
        >
          <Zap className="w-4 h-4" />
          {t('benchmark.sidebar.newExperiment')}
        </button>
      </div>

      {/* 历史记录列表 */}
      <div className="flex-1 overflow-y-auto p-3">
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 px-2">
          {t('benchmark.sidebar.history')}
        </h3>

        {results.length === 0 ? (
          <div className="text-center py-8 text-slate-400">
            <Clock className="w-10 h-10 mx-auto mb-2 opacity-50" />
            <p className="text-sm">{t('benchmark.sidebar.empty')}</p>
          </div>
        ) : (
          <div className="space-y-2">
            {results.map((result) => {
              const isSelected = selectedResult === result.name
              const isDeleting = deleteConfirm === result.name
              // 当前项被选中且正在加载时显示加载指示器
              const isItemLoading = isSelected && isLoading

              return (
                <div
                  key={result.name}
                  onClick={() => onSelectResult(result.name)}
                  className={`
                    group relative p-3 rounded-xl cursor-pointer transition-all
                    ${isSelected
                      ? 'bg-blue-600 text-white shadow-md'
                      : 'bg-white border border-slate-200 hover:border-blue-300 hover:shadow-sm'
                    }
                  `}
                >
                  {/* 左侧选中指示条 */}
                  {isSelected && (
                    <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-8 bg-white rounded-r-full" />
                  )}

                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <div className={`font-medium text-sm truncate ${isSelected ? 'text-white' : 'text-slate-700'}`}>
                        {formatDisplayName(result.name)}
                      </div>
                      <div className={`text-xs mt-0.5 ${isSelected ? 'text-blue-100' : 'text-slate-400'}`}>
                        {result.created}
                      </div>
                      {result.human_file && (
                        <div className={`text-xs mt-0.5 ${isSelected ? 'text-blue-200' : 'text-slate-400'}`}>
                          📄 {result.human_file}
                        </div>
                      )}
                    </div>

                    {/* 加载指示器（仅选中项加载时显示）或删除按钮 */}
                    {isItemLoading ? (
                      <div className="p-1.5">
                        <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      </div>
                    ) : (
                      <button
                        onClick={(e) => handleDeleteClick(e, result.name)}
                        className={`
                          opacity-0 group-hover:opacity-100 p-1.5 rounded-lg transition-all
                          ${isDeleting
                            ? 'bg-red-500 text-white opacity-100'
                            : isSelected
                              ? 'hover:bg-blue-500 text-white'
                              : 'hover:bg-red-50 text-slate-400 hover:text-red-500'
                          }
                        `}
                        title={isDeleting ? t('benchmark.sidebar.confirmDelete') : t('benchmark.sidebar.delete')}
                      >
                        {isDeleting ? <Check className="w-3.5 h-3.5" /> : <Trash2 className="w-3.5 h-3.5" />}
                      </button>
                    )}
                  </div>

                  {/* Batch 信息 */}
                  {result.batch_ids && result.batch_ids.length > 0 && (
                    <div className={`text-xs mt-1.5 flex flex-wrap gap-1 ${isSelected ? 'text-blue-200' : 'text-slate-400'}`}>
                      {result.batch_ids.map((batch) => (
                        <span key={batch} className="px-1.5 py-0.5 rounded bg-black/5">
                          {batch.replace('batch_', '')}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 底部信息 */}
      <div className="p-3 border-t border-slate-200 text-xs text-slate-400 text-center">
        {t('benchmark.sidebar.footer')}
      </div>
    </div>
  )
}
