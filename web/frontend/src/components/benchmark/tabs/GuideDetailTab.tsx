import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { MessageSquare, Send, Trash2, Edit2, Save } from 'lucide-react'
import { GuideDetail, ScoreUpdate } from '@/services/api'
import { api } from '@/services/api'
import { toast } from 'react-hot-toast'

// 临时修改的分数记录
type ModifiedScores = Record<string, number>

interface GuideDetailTabProps {
  guide: GuideDetail
  batchIds: string[]
  xlsxFile: string
  modifiedScores: ModifiedScores
  onScoreChange: (guideId: string, itemId: string, field: string, newValue: number) => void
  onClearModifiedScores: (guideId: string) => void
  onRefresh?: () => void
  raterNames?: string[]
  hasPersistedChanges?: boolean
}

// 可编辑分数组件
function EditableScore({
  value,
  originalValue,
  onChange,
  onBlur,
  isModel = false
}: {
  value: number | null | undefined
  originalValue: number | null | undefined
  onChange: (newValue: number) => void
  onBlur?: () => void
  isModel?: boolean
}) {
  const [isEditing, setIsEditing] = useState(false)
  const [tempValue, setTempValue] = useState('')

  const handleStartEdit = () => {
    setTempValue(value?.toString() || '')
    setIsEditing(true)
  }

  const handleSave = () => {
    const num = parseInt(tempValue)
    if (!isNaN(num) && num >= 1 && num <= 7) {
      if (num !== value) {
        onChange(num)
      }
    } else {
      setTempValue(value?.toString() || '')
    }
    setIsEditing(false)
    onBlur?.()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSave()
    } else if (e.key === 'Escape') {
      setTempValue(value?.toString() || '')
      setIsEditing(false)
      onBlur?.()
    }
  }

  const isModified = value !== originalValue

  if (isEditing) {
    return (
      <input
        type="number"
        min={1}
        max={7}
        value={tempValue}
        onChange={(e) => setTempValue(e.target.value)}
        onBlur={handleSave}
        onKeyDown={handleKeyDown}
        autoFocus
        className="w-12 px-1 py-0.5 text-center text-sm font-mono border-2 border-blue-400 rounded focus:outline-none focus:ring-2 focus:ring-blue-200"
      />
    )
  }

  if (value === null || value === undefined) {
    return (
      <span
        onClick={handleStartEdit}
        className="text-slate-300 cursor-pointer hover:bg-slate-100 px-2 py-0.5 rounded transition-colors"
      >
        —
      </span>
    )
  }

  return (
    <span
      onClick={handleStartEdit}
      className={`
        cursor-pointer px-2 py-0.5 rounded transition-all duration-200 font-medium font-mono select-none
        ${isModel ? 'text-blue-700' : 'text-slate-700'}
        ${isModified
          ? 'bg-amber-50 text-amber-700 ring-2 ring-amber-400 ring-offset-1 font-bold shadow-sm'
          : 'hover:bg-slate-100 hover:ring-1 hover:ring-slate-200'
        }
      `}
      title={isModified ? `原始值: ${originalValue ?? '-'}` : '点击修改'}
    >
      {value}
    </span>
  )
}

// 备注框组件
function CommentBox({ guideId, xlsxFile, initialComments }: { guideId: string; xlsxFile: string; initialComments: any[] }) {
  const [comments, setComments] = useState<any[]>(initialComments || [])
  const [newComment, setNewComment] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editContent, setEditContent] = useState('')

  // 加载评论数据
  useEffect(() => {
    setComments(initialComments || [])
  }, [initialComments])

  const handleSubmit = async () => {
    if (!newComment.trim()) return

    setIsSubmitting(true)
    try {
      const result = await api.addComment(xlsxFile, guideId, newComment.trim())
      setComments([...comments, result.comment])
      setNewComment('')
      toast.success('备注已添加')
    } catch (error) {
      toast.error('添加失败: ' + (error as Error).message)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await api.deleteComment(xlsxFile, guideId, id)
      setComments(comments.filter(c => c.id !== id))
      toast.success('备注已删除')
    } catch (error) {
      toast.error('删除失败: ' + (error as Error).message)
    }
  }

  const handleEdit = (id: string) => {
    const comment = comments.find(c => c.id === id)
    if (comment) {
      setEditingId(id)
      setEditContent(comment.content)
    }
  }

  const handleSaveEdit = async (id: string) => {
    try {
      const result = await api.updateComment(xlsxFile, guideId, id, editContent)
      setComments(comments.map(c =>
        c.id === id ? result.comment : c
      ))
      setEditingId(null)
      toast.success('备注已更新')
    } catch (error) {
      toast.error('更新失败: ' + (error as Error).message)
    }
  }

  return (
    <div className="bg-slate-50 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
        <MessageSquare className="w-4 h-4 text-blue-500" />
        备注
      </h3>

      {/* 评论列表 */}
      <div className="space-y-3 mb-3 max-h-60 overflow-y-auto">
        {comments.length === 0 ? (
          <p className="text-sm text-slate-400 text-center py-4">暂无备注</p>
        ) : (
          comments.map(comment => (
            <div key={comment.id} className="bg-white rounded-lg p-3 border border-slate-200">
              {editingId === comment.id ? (
                <div className="space-y-2">
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    className="w-full px-2 py-1.5 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                    rows={3}
                  />
                  <div className="flex justify-end gap-2">
                    <button
                      onClick={() => setEditingId(null)}
                      className="text-xs px-2 py-1 rounded bg-slate-100 text-slate-600 hover:bg-slate-200"
                    >
                      取消
                    </button>
                    <button
                      onClick={() => handleSaveEdit(comment.id)}
                      className="text-xs px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700"
                    >
                      保存
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-start justify-between mb-1">
                    <span className="text-xs font-medium text-slate-600">
                      {comment.author}
                    </span>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => handleEdit(comment.id)}
                        className="p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600"
                      >
                        <Edit2 className="w-3 h-3" />
                      </button>
                      <button
                        onClick={() => handleDelete(comment.id)}
                        className="p-1 rounded hover:bg-red-50 text-slate-400 hover:text-red-500"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                  <p className="text-sm text-slate-700">{comment.content}</p>
                  <p className="text-xs text-slate-400 mt-1">
                    {new Date(comment.created_at).toLocaleString('zh-CN')}
                  </p>
                </>
              )}
            </div>
          ))
        )}
      </div>

      {/* 添加新评论 */}
      <div className="flex gap-2">
        <input
          type="text"
          value={newComment}
          onChange={(e) => setNewComment(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), handleSubmit())}
          placeholder="添加备注..."
          className="flex-1 px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={handleSubmit}
          disabled={!newComment.trim() || isSubmitting}
          className="px-3 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
        >
          {isSubmitting ? (
            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          ) : (
            <Send className="w-4 h-4" />
          )}
        </button>
      </div>
    </div>
  )
}

export function GuideDetailTab({ guide, batchIds, xlsxFile, modifiedScores, onScoreChange, onClearModifiedScores, onRefresh, raterNames, hasPersistedChanges }: GuideDetailTabProps) {
  const { t } = useTranslation()
  const [isSaving, setIsSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const getScoreKey = (itemId: string, field: string) => `${guide.guide_id}::${itemId}::${field}`

  const getCurrentScore = (itemId: string, field: string, original: number | null | undefined) => {
    const key = getScoreKey(itemId, field)
    return modifiedScores[key] ?? original
  }

  const handleScoreChange = (itemId: string, field: string, newValue: number) => {
    onScoreChange(guide.guide_id, itemId, field, newValue)
  }

  const handleSaveGuide = async () => {
    setIsSaving(true)
    setSaveError(null)

    try {
      const updates: ScoreUpdate[] = []

      guide.items.forEach(item => {
        const fields = ['human_score1', 'human_score2', 'batch1_model_score', 'batch2_model_score'] as const

        fields.forEach(field => {
          const key = getScoreKey(item.item_id, field)
          if (key in modifiedScores) {
            updates.push({
              guide_id: guide.guide_id,
              item_id: item.item_id,
              field,
              value: modifiedScores[key]
            })
          }
        })
      })

      if (updates.length === 0) {
        toast.success('没有需要保存的修改')
        return
      }

      await api.saveBenchmarkChanges(xlsxFile, updates)

      onClearModifiedScores(guide.guide_id)

      onRefresh?.()

      toast.success(t('benchmark.header.saved'))
    } catch (error) {
      setSaveError((error as Error).message)
      toast.error('保存失败: ' + (error as Error).message)
    } finally {
      setIsSaving(false)
    }
  }

  const hasModifiedScores = guide.items.some(item => {
    const keys = [
      getScoreKey(item.item_id, 'human_score1'),
      getScoreKey(item.item_id, 'human_score2'),
      getScoreKey(item.item_id, 'batch1_model_score'),
      getScoreKey(item.item_id, 'batch2_model_score')
    ]
    return keys.some(key => key in modifiedScores)
  })



  return (
    <div className="p-6">
      {/* 指南头部 */}
      <div className="mb-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <h2 className="text-lg font-semibold text-slate-800 mb-1 flex items-center gap-2">
              {hasPersistedChanges && (
                <span className="w-2 h-2 rounded-full bg-amber-500 flex-shrink-0" title="有未保存的更改" />
              )}
              {t('benchmark.tabs.guide')} {guide.seq_no}: {guide.guide_name}
            </h2>
            <p className="text-sm text-slate-500">ID: {guide.guide_id}</p>
          </div>
          <button
            onClick={handleSaveGuide}
            disabled={isSaving || (!hasModifiedScores && !hasPersistedChanges)}
            className={`
              px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-all duration-200
              ${isSaving
                ? 'bg-slate-100 text-slate-400 cursor-wait'
                : hasModifiedScores
                  ? 'bg-gradient-to-r from-blue-600 to-blue-700 hover:from-blue-700 hover:to-blue-800 text-white shadow-md hover:shadow-lg'
                  : 'bg-slate-100 text-slate-400 cursor-not-allowed'
              }
              disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-sm
            `}
            title={hasModifiedScores ? '保存当前指南的修改' : '没有需要保存的修改'}
          >
            {isSaving ? (
              <>
                <div className="w-4 h-4 border-2 border-slate-400/30 border-t-slate-400 rounded-full animate-spin" />
                保存中...
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                保存
              </>
            )}
          </button>
        </div>
        {saveError && (
          <div className="mt-2 p-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2">
            <span className="text-red-600 text-sm">{saveError}</span>
          </div>
        )}
      </div>

      {/* 评分表格 */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden mb-6">
        <table className="w-full text-sm">
          <colgroup>
            <col style={{ width: '25%' }} />
            <col style={{ width: '18.75%' }} />
            <col style={{ width: '18.75%' }} />
            <col style={{ width: '18.75%' }} />
            <col style={{ width: '18.75%' }} />
          </colgroup>
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200">
              <th className="py-3 px-4 text-left font-semibold text-slate-700 text-xs">
                {t('benchmark.details.item')}
              </th>
              <th className="py-3 px-2 text-center font-semibold text-slate-700 text-xs">
                <div className="flex flex-col items-center gap-0.5">
                  <span className="text-[9px] uppercase tracking-wider text-blue-500 font-bold">
                    {t('benchmark.details.human')}
                  </span>
                  <span className="text-xs text-slate-600">
                    {raterNames?.[0] || t('benchmark.details.rater1')}
                  </span>
                </div>
              </th>
              <th className="py-3 px-2 text-center font-semibold text-slate-700 text-xs">
                <div className="flex flex-col items-center gap-0.5">
                  <span className="text-[9px] uppercase tracking-wider text-blue-500 font-bold">
                    {t('benchmark.details.human')}
                  </span>
                  <span className="text-xs text-slate-600">
                    {raterNames?.[1] || t('benchmark.details.rater2')}
                  </span>
                </div>
              </th>
              <th className="py-3 px-2 text-center font-semibold text-slate-700 text-xs">
                <div className="flex flex-col items-center gap-0.5">
                  <span className="text-[9px] uppercase tracking-wider text-violet-500 font-bold">
                    {t('benchmark.details.model')}
                  </span>
                  <span className="text-xs text-slate-600 whitespace-normal leading-tight">
                    {batchIds[0]?.replace('batch_', '') || t('benchmark.details.batch1')}
                  </span>
                </div>
              </th>
              <th className="py-3 px-2 text-center font-semibold text-slate-700 text-xs">
                <div className="flex flex-col items-center gap-0.5">
                  <span className="text-[9px] uppercase tracking-wider text-blue-500 font-bold">
                    {t('benchmark.details.model')}
                  </span>
                  <span className="text-xs text-slate-600 whitespace-normal leading-tight">
                    {batchIds[1]?.replace('batch_', '') || t('benchmark.details.batch2')}
                  </span>
                </div>
              </th>
            </tr>
          </thead>
          <tbody>
            {guide.items.map((item, idx) => {
              const currentHuman1 = getCurrentScore(item.item_id, 'human_score1', item.human_score1)
              const currentHuman2 = getCurrentScore(item.item_id, 'human_score2', item.human_score2)
              const currentBatch1 = getCurrentScore(item.item_id, 'batch1_model_score', item.batch1_model_score)
              const currentBatch2 = getCurrentScore(item.item_id, 'batch2_model_score', item.batch2_model_score)

              const scores = [
                currentHuman1,
                currentHuman2,
                currentBatch1,
                currentBatch2
              ].filter((s): s is number => s !== null && s !== undefined)

              const minScore = scores.length > 0 ? Math.min(...scores) : 0
              const maxScore = scores.length > 0 ? Math.max(...scores) : 0
              const hasLargeDiff = maxScore - minScore > 2

              return (
                <tr
                  key={item.item_id}
                  className={`
                    border-b transition-colors
                    ${hasLargeDiff
                      ? 'bg-rose-50/80 hover:bg-rose-100/60'
                      : idx % 2 === 0
                        ? 'bg-white hover:bg-slate-50/50'
                        : 'bg-slate-50/30 hover:bg-slate-50/60'
                    }
                  `}
                >
                  <td className="py-3 px-4">
                    <div className="flex items-center gap-2">
                      <span className={`w-6 h-6 flex items-center justify-center rounded text-[10px] font-bold ${hasLargeDiff
                        ? 'bg-rose-200 text-rose-700'
                        : 'bg-slate-100 text-slate-500'
                        }`}>
                        {idx + 1}
                      </span>
                      <span className={`text-sm ${hasLargeDiff ? 'text-rose-900 font-medium' : 'text-slate-700'}`}>
                        {item.item_name}
                      </span>
                    </div>
                  </td>
                  <td className="py-3 px-2 text-center">
                    <EditableScore
                      value={currentHuman1}
                      originalValue={item.human_score1_original ?? item.human_score1}
                      onChange={(val) => handleScoreChange(item.item_id, 'human_score1', val)}
                    />
                  </td>
                  <td className="py-3 px-2 text-center">
                    <EditableScore
                      value={currentHuman2}
                      originalValue={item.human_score2_original ?? item.human_score2}
                      onChange={(val) => handleScoreChange(item.item_id, 'human_score2', val)}
                    />
                  </td>
                  <td className="py-3 px-2 text-center">
                    <EditableScore
                      value={currentBatch1}
                      originalValue={item.batch1_model_score_original ?? item.batch1_model_score}
                      onChange={(val) => handleScoreChange(item.item_id, 'batch1_model_score', val)}
                      isModel
                    />
                  </td>
                  <td className="py-3 px-2 text-center">
                    <EditableScore
                      value={currentBatch2}
                      originalValue={item.batch2_model_score_original ?? item.batch2_model_score}
                      onChange={(val) => handleScoreChange(item.item_id, 'batch2_model_score', val)}
                      isModel
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* 备注框 */}
      <CommentBox guideId={guide.guide_id} xlsxFile={xlsxFile} initialComments={guide.comments || []} />
    </div>
  )
}
