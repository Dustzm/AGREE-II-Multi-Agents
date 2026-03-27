import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { FileText } from 'lucide-react'
import { BenchmarkSummary } from '@/services/api'
import { ExperimentHeader } from './ExperimentHeader'
import { TabBar } from './TabBar'
import { NewExperimentForm } from './NewExperimentForm'
import { ConsistencyTab } from './tabs/ConsistencyTab'
import { GuideDetailTab } from './tabs/GuideDetailTab'

type WorkspaceMode = 'empty' | 'new' | 'detail'
type ViewMode = 'consistency' | 'guide'

// 临时修改的分数记录
type ModifiedScores = Record<string, number>

interface WorkspaceProps {
  // 数据状态
  mode: WorkspaceMode
  summary: BenchmarkSummary | null
  xlsxFile: string
  batchIds: string[]

  // 新建实验相关
  humanFiles: any[]
  batches: any[]
  isRunning: boolean

  // 加载状态
  isLoadingDetail: boolean

  // 编辑状态
  hasChanges: boolean
  isSaving: boolean

  // 评分者信息
  raterNames?: string[]

  // 回调
  onRun: (humanFile: string, batches: string[], threshold: number) => Promise<void>
  onSave: () => void
  onDownload: () => void
  onDelete: () => void
  onRefresh?: () => void
}

export function Workspace({
  mode,
  summary,
  xlsxFile,
  batchIds,
  humanFiles,
  batches,
  isRunning,
  isLoadingDetail,
  hasChanges,
  isSaving,
  onRun,
  onSave,
  onDownload,
  onDelete,
  onRefresh
}: WorkspaceProps) {
  const raterNames = summary?.rater_names || []
  const { t } = useTranslation()

  // 视图模式：一致性分析 或 指南详情
  const [viewMode, setViewMode] = useState<ViewMode>('consistency')
  // 选中的指南索引
  const [selectedGuideIndex, setSelectedGuideIndex] = useState(0)
  // 当前指南的自动保存状态（已弃用自动保存，固定为 false）
  const guideIsSaving = false
  const guideHasUnsaved = false
  // 临时修改的分数状态（所有指南）
  const [modifiedScores, setModifiedScores] = useState<ModifiedScores>({})

  // 当切换到不同实验时，重置视图模式和修改状态
  useEffect(() => {
    setViewMode('consistency')
    setSelectedGuideIndex(0)
    setModifiedScores({})
  }, [xlsxFile])

  // 检查指南是否有持久化的修改
  const hasPersistedChanges = (guideId: string) => {
    if (!summary?.guide_details) return false
    const guide = summary.guide_details.find(g => g.guide_id === guideId)
    if (!guide) return false

    return guide.items.some(item => {
      return (
        (item.human_score1_original !== null && item.human_score1_original !== undefined &&
          item.human_score1 !== item.human_score1_original) ||
        (item.human_score2_original !== null && item.human_score2_original !== undefined &&
          item.human_score2 !== item.human_score2_original) ||
        (item.batch1_model_score_original !== null && item.batch1_model_score_original !== undefined &&
          item.batch1_model_score !== item.batch1_model_score_original) ||
        (item.batch2_model_score_original !== null && item.batch2_model_score_original !== undefined &&
          item.batch2_model_score !== item.batch2_model_score_original)
      )
    })
  }

  // 检查指南是否有任何修改（持久化或临时）
  const guideHasChanges = (guideId: string) => {
    if (!summary?.guide_details) return false
    const guide = summary.guide_details.find(g => g.guide_id === guideId)
    if (!guide) return false

    // 检查持久化修改
    const hasPersisted = hasPersistedChanges(guideId)

    // 检查临时修改
    const hasTemp = guide.items.some(item => {
      const keys = [
        `${guideId}::${item.item_id}::human_score1`,
        `${guideId}::${item.item_id}::human_score2`,
        `${guideId}::${item.item_id}::batch1_model_score`,
        `${guideId}::${item.item_id}::batch2_model_score`
      ]
      return keys.some(key => key in modifiedScores)
    })

    return hasPersisted || hasTemp
  }
  // 处理分数修改
  const handleScoreChange = (guideId: string, itemId: string, field: string, newValue: number) => {
    const key = `${guideId}::${itemId}::${field}`
    setModifiedScores(prev => ({
      ...prev,
      [key]: newValue
    }))
  }

  // 清空指定指南的临时修改
  const clearGuideModifiedScores = (guideId: string) => {
    setModifiedScores(prev => {
      const newScores = { ...prev }
      const guide = summary?.guide_details?.find(g => g.guide_id === guideId)
      if (guide) {
        guide.items.forEach(item => {
          const keys = [
            `${guideId}::${item.item_id}::human_score1`,
            `${guideId}::${item.item_id}::human_score2`,
            `${guideId}::${item.item_id}::batch1_model_score`,
            `${guideId}::${item.item_id}::batch2_model_score`
          ]
          keys.forEach(key => {
            delete newScores[key]
          })
        })
      }
      return newScores
    })
  }

  // 导航到上一指南
  const handlePrevGuide = () => {
    if (selectedGuideIndex > 0) {
      setSelectedGuideIndex(selectedGuideIndex - 1)
    }
  }

  // 导航到下一指南
  const handleNextGuide = () => {
    const guideCount = summary?.guide_details?.length || 0
    if (selectedGuideIndex < guideCount - 1) {
      setSelectedGuideIndex(selectedGuideIndex + 1)
    }
  }

  // 空状态
  if (mode === 'empty') {
    return (
      <div className="flex-1 flex items-center justify-center bg-gradient-to-br from-slate-50 to-blue-50/30">
        <div className="text-center">
          <FileText className="w-20 h-20 mx-auto mb-4 text-slate-300" />
          <h2 className="text-xl font-semibold text-slate-700 mb-2">
            {t('benchmark.workspace.empty.title')}
          </h2>
          <p className="text-slate-500 mb-6">
            {t('benchmark.workspace.empty.description')}
          </p>
        </div>
      </div>
    )
  }

  // 新建实验表单
  if (mode === 'new') {
    return (
      <div className="flex-1 bg-slate-50">
        <NewExperimentForm
          humanFiles={humanFiles}
          batches={batches}
          isRunning={isRunning}
          onRun={onRun}
        />
      </div>
    )
  }

  // 实验详情
  if (mode === 'detail') {
    // 加载状态：显示骨架屏但保留现有内容
    if (isLoadingDetail && !summary) {
      return (
        <div className="flex-1 flex items-center justify-center bg-white">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
            <p className="text-sm text-slate-500">加载中...</p>
          </div>
        </div>
      )
    }

    if (!summary) return null

    const guideDetails = summary.guide_details || []

    return (
      <div className="flex-1 flex flex-col bg-white relative">
        {/* 头部 */}
        <ExperimentHeader
          filename={xlsxFile}
          createdAt={new Date().toLocaleString('zh-CN')}
          consistency={summary.overall_consistency}
          guideCount={summary.total_guides}
          hasChanges={hasChanges}
          isSaving={isSaving}
          onSave={onSave}
          onDownload={onDownload}
          onDelete={onDelete}
        />

        {/* 标签页导航 */}
        <TabBar
          viewMode={viewMode}
          selectedGuideIndex={selectedGuideIndex}
          guideDetails={guideDetails}
          onViewModeChange={setViewMode}
          onGuideChange={setSelectedGuideIndex}
          onPrevGuide={handlePrevGuide}
          onNextGuide={handleNextGuide}
          hasPersistedChanges={guideHasChanges}
          guideIsSaving={guideIsSaving}
          guideHasUnsaved={guideHasUnsaved}
        />

        {/* 内容区 */}
        <div className="flex-1 overflow-auto">
          {viewMode === 'consistency' ? (
            <ConsistencyTab summary={summary} xlsxFile={xlsxFile} />
          ) : (
            <GuideDetailTab
              guide={guideDetails[selectedGuideIndex]}
              batchIds={batchIds}
              xlsxFile={xlsxFile}
              modifiedScores={modifiedScores}
              onScoreChange={handleScoreChange}
              onClearModifiedScores={clearGuideModifiedScores}
              onRefresh={onRefresh}
              raterNames={raterNames}
              hasPersistedChanges={guideHasChanges(guideDetails[selectedGuideIndex]?.guide_id || '')}
            />
          )}
        </div>

        {/* 加载覆盖层 - 使用淡入效果 */}
        {isLoadingDetail && (
          <div className="absolute inset-0 bg-white/80 backdrop-blur-[2px] flex items-center justify-center z-10 animate-in fade-in duration-200">
            <div className="flex flex-col items-center gap-3">
              <div className="w-8 h-8 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
              <p className="text-sm text-slate-600 font-medium">加载中...</p>
            </div>
          </div>
        )}
      </div>
    )
  }

  return null
}
