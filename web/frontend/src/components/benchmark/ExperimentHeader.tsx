import { useTranslation } from 'react-i18next'
import { Download, Trash2, Save, Calendar, FileText } from 'lucide-react'

interface ExperimentHeaderProps {
  filename: string
  createdAt: string
  consistency: number
  guideCount: number
  hasChanges: boolean
  isSaving: boolean
  onSave: () => void
  onDownload: () => void
  onDelete: () => void
}

export function ExperimentHeader({
  filename,
  createdAt,
  consistency,
  guideCount,
  hasChanges,
  isSaving,
  onSave,
  onDownload,
  onDelete
}: ExperimentHeaderProps) {
  const { t } = useTranslation()

  const handleDelete = () => {
    if (confirm(t('benchmark.header.confirmDelete'))) {
      onDelete()
    }
  }

  return (
    <div className="bg-white border-b border-slate-200 px-6 py-4">
      <div className="flex items-start justify-between">
        {/* 左侧信息 */}
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-lg font-semibold text-slate-800">
              {filename}
            </h1>
            {hasChanges && (
              <span className="px-2 py-0.5 bg-amber-100 text-amber-700 text-xs font-medium rounded-full">
                {t('benchmark.header.unsaved')}
              </span>
            )}
          </div>

          <div className="flex items-center gap-4 text-sm text-slate-500">
            <div className="flex items-center gap-1.5">
              <Calendar className="w-4 h-4" />
              <span>{createdAt}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <FileText className="w-4 h-4" />
              <span>{guideCount} {t('benchmark.header.guides')}</span>
            </div>
            <div className={`
              px-2 py-0.5 rounded-full font-medium
              ${consistency >= 0.8 ? 'bg-emerald-100 text-emerald-600' : ''}
              ${consistency >= 0.6 && consistency < 0.8 ? 'bg-amber-100 text-amber-600' : ''}
              ${consistency < 0.6 ? 'bg-rose-100 text-rose-600' : ''}
            `}>
              {t('benchmark.header.consistency')}: {(consistency * 100).toFixed(1)}%
            </div>
          </div>
        </div>

        {/* 右侧操作按钮 */}
        <div className="flex items-center gap-2">
          {hasChanges && (
            <button
              onClick={onSave}
              disabled={isSaving}
              className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors flex items-center gap-2 text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSaving ? (
                <>
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  {t('benchmark.header.saving')}
                </>
              ) : (
                <>
                  <Save className="w-4 h-4" />
                  {t('benchmark.header.save')}
                </>
              )}
            </button>
          )}

          <button
            onClick={onDownload}
            className="p-2 rounded-lg hover:bg-slate-100 text-slate-500 hover:text-slate-700 transition-colors"
            title={t('benchmark.header.download')}
          >
            <Download className="w-4 h-4" />
          </button>

          <button
            onClick={handleDelete}
            className="p-2 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-500 transition-colors"
            title={t('benchmark.header.delete')}
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
