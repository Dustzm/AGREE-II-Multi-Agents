import { formatDate } from '@/i18n/format'
import { notifyError } from '@/i18n/notify'
/**
 * Reports - 评价报告页面
 * 
 * 展示所有评价报告，支持查看详情
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { FileText, Loader2, ArrowLeft, CheckCircle2, XCircle, AlertCircle, RefreshCw, LayoutGrid, List, FileBarChart, Eye, Folder, Home, ChevronRight, Info, Quote, Sparkles, Scale } from 'lucide-react'
import { api, type ReportSummary, type ReportDetail, type BatchFolderInfo, type StructuredReasoning, type ItemEvaluation, type EvidenceMatch } from '@/services/api'
import { cn } from '@/utils/cn'
import { citationHighlights, citationPage, reasonCitations } from '@/utils/citations'
import { EvidenceViewer } from '@/components/EvidenceViewer'
import { SourceDrawer } from '@/components/SourceDrawer'
// import { exportToPdf } from '@/utils/exportPdf'


// ============================================================================
// ItemEvaluationCard 组件 - 展示单个条目的评价详情
// ============================================================================

interface ItemEvaluationCardProps {
    item: ItemEvaluation
    onReasonClick: (chunkIds: string[], point: string, explanation?: string) => void  // 直接传递 chunk_id
    onLocate: (highlights: EvidenceMatch[], point: string, explanation?: string) => void  // 按引用原句的页码与坐标高亮
}

function ItemEvaluationCard({ item, onReasonClick, onLocate }: ItemEvaluationCardProps) {
    const { t, i18n } = useTranslation()

    // 判断 step3_reasoning 是结构化格式还是旧的字符串格式
    const isStructuredReasoning = (reasoning: unknown): reasoning is StructuredReasoning => {
        return typeof reasoning === 'object' && reasoning !== null && 'reasons' in reasoning
    }

    const structuredReasoning = isStructuredReasoning(item.step3_reasoning) ? item.step3_reasoning : null
    const legacyReasoning = !structuredReasoning && typeof item.step3_reasoning === 'string' ? item.step3_reasoning : null

    // 获取当前评分匹配的评分准则
    const getMatchedPrincipleIndex = (): number => {
        if (!item.scoring_principle || item.score === null) return -1
        for (let i = 0; i < item.scoring_principle.length; i++) {
            const range = item.scoring_principle[i].range
            if (range.includes('-')) {
                const [low, high] = range.split('-').map(Number)
                if (item.score >= low && item.score <= high) return i
            } else if (Number(range) === item.score) {
                return i
            }
        }
        return -1
    }

    const matchedIndex = getMatchedPrincipleIndex()
    // 逐句定位的引用；旧结果没有 citations，理由仍按 chunk_id 定位
    const citations = item.citations ?? null

    return (
        <details className="group border border-border/50 rounded-lg overflow-hidden transition-all duration-300 open:shadow-md">
            <summary className="flex items-center gap-3 p-4 cursor-pointer hover:bg-muted/30 transition-colors relative">
                {/* 左侧彩色条 - 根据分数变化颜色 */}
                <div className={cn(
                    "absolute left-0 top-0 bottom-0 w-1",
                    item.score && item.score >= 5 ? "bg-green-500" : item.score && item.score <= 3 ? "bg-red-400" : "bg-yellow-400"
                )} />

                <span className="px-2 py-0.5 bg-muted text-muted-foreground text-xs font-mono rounded border border-border/50 ml-1">
                    {item.item_id}
                </span>
                <span className="text-sm font-semibold flex-1 line-clamp-1">{item.title}</span>
                <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground hidden group-open:inline-block">{t('ui.score')}</span>
                    <span className={cn(
                        "text-lg font-bold font-mono tracking-tight",
                        item.score && item.score >= 5 ? "text-green-600" : item.score && item.score <= 3 ? "text-red-500" : "text-yellow-600"
                    )}>
                        {item.score !== null ? `${item.score}` : '-'}
                        <span className="text-sm font-normal text-muted-foreground/60 ml-0.5">/7</span>
                    </span>
                </div>
            </summary>

            <div className="px-4 pb-4 pt-2 border-t border-border/30 bg-muted/20 space-y-4">
                {/* 评分准则 - 默认展开 */}
                {/* 评分准则 - 默认展开 */}
                {item.scoring_principle && item.scoring_principle.length > 0 && (
                    <div className="bg-white/40 rounded-lg p-3 border border-border/30">
                        <h4 className="text-[11px] font-bold text-muted-foreground/60 uppercase tracking-wider mb-2 flex items-center gap-1.5 pl-1">
                            <FileBarChart className="h-3 w-3" />
                            {t('reports.scoring.criteria')}
                        </h4>
                        <div className="space-y-0.5">
                            {item.scoring_principle.map((sp, idx) => (
                                <div
                                    key={idx}
                                    className={cn(
                                        "flex items-start gap-3 px-3 py-2.5 text-xs transition-all relative overflow-hidden",
                                        matchedIndex === idx
                                            ? "bg-slate-100/80 text-foreground rounded-r-md border-l-[3px] border-primary font-medium"
                                            : "text-muted-foreground/70 hover:bg-muted/30 rounded-md hover:text-muted-foreground"
                                    )}
                                >
                                    <span className={cn(
                                        "font-mono shrink-0 w-8 text-center pt-0.5",
                                        matchedIndex === idx ? "text-primary font-bold" : "opacity-80"
                                    )}>
                                        {sp.range}
                                    </span>
                                    <span className={cn(
                                        "flex-1 leading-relaxed",
                                        matchedIndex === idx ? "" : ""
                                    )}>
                                        {sp.condition}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* 结构化推理理由 - 新格式 */}
                {structuredReasoning && (
                    <div className="space-y-4">
                        {/* 总结 - 优化视觉 */}
                        <div className="relative overflow-hidden bg-gradient-to-br from-indigo-50/80 to-purple-50/60 rounded-xl p-4 border border-indigo-100/50 shadow-[0_2px_8px_-4px_rgba(0,0,0,0.05)]">
                            <div className="absolute top-0 right-0 p-3 opacity-10">
                                <Quote className="h-16 w-16 text-indigo-900 rotate-12" />
                            </div>
                            <div className="relative flex gap-3">
                                <div className="shrink-0 mt-0.5">
                                    <div className="h-8 w-8 rounded-full bg-indigo-100/80 flex items-center justify-center text-indigo-600">
                                        <Sparkles className="h-4 w-4" />
                                    </div>
                                </div>
                                <div className="space-y-1">
                                    <h4 className="text-xs font-bold text-indigo-900/50 uppercase tracking-wider">{t('ui.evaluationSummary')}</h4>
                                    <p className="text-sm font-medium text-slate-700 leading-relaxed">
                                        {structuredReasoning.summary}
                                    </p>
                                </div>
                            </div>
                        </div>

                        {/* 分条理由 - 可点击 */}
                        <div className="space-y-2">
                            {structuredReasoning.reasons.map((reason, idx) => {
                                // 优先使用 evidence_chunks，兼容旧版 evidence_refs
                                const hasEvidence = (reason.evidence_chunks && reason.evidence_chunks.length > 0) ||
                                    (reason.evidence_refs && reason.evidence_refs.length > 0)
                                const chunkIds = reason.evidence_chunks ||
                                    (reason.evidence_refs ? item.evidence_sources?.slice(0, reason.evidence_refs.length) : []) ||
                                    []
                                // 有引用时只计、只高亮落在这些 chunk 内的原句；一句都没有时回退按 chunk 定位
                                const linked = citations ? reasonCitations(citations, chunkIds) : []
                                const highlights = linked.flatMap(citationHighlights)

                                return (
                                    <div
                                        key={idx}
                                        onClick={() => {
                                            if (highlights.length > 0) {
                                                onLocate(highlights, reason.point, reason.explanation)
                                            } else if (chunkIds.length > 0) {
                                                onReasonClick(chunkIds, reason.point, reason.explanation)
                                            }
                                        }}
                                        className={cn(
                                            "bg-white rounded-lg border border-border/40 transition-all shadow-sm relative overflow-hidden",
                                            hasEvidence
                                                ? "cursor-pointer hover:border-primary/40 hover:shadow-md hover:-translate-y-0.5 group/reason"
                                                : "opacity-80"
                                        )}
                                    >
                                        {/* 左侧装饰条 */}
                                        <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-blue-400/20 to-transparent" />

                                        <div className="p-3.5">
                                            <div className="flex items-start justify-between gap-3 mb-2">
                                                <div className="flex items-center gap-2">
                                                    <div className="h-5 w-5 rounded-full bg-blue-50 flex items-center justify-center shrink-0 border border-blue-100">
                                                        <div className="h-2 w-2 rounded-full bg-blue-500" />
                                                    </div>
                                                    <span className="text-sm font-bold text-slate-800">
                                                        {reason.point}
                                                    </span>
                                                    {/* 验证提示 - 移至标题旁，更紧凑 */}
                                                    {hasEvidence && (
                                                        <span className="flex items-center gap-1 text-[10px] text-primary/60 font-medium uppercase tracking-wide opacity-0 group-hover/reason:opacity-100 transition-opacity ml-2">
                                                            <Eye className="h-3 w-3" />
                                                            {t('ui.verify')}
                                                        </span>
                                                    )}
                                                </div>

                                                {/* 证据引用标记 - 论文风格 */}
                                                {hasEvidence && (
                                                    <div className="flex items-center gap-1.5 px-2 py-0.5 bg-slate-100 rounded text-[10px] font-medium text-slate-500 border border-slate-200 group-hover/reason:bg-primary/5 group-hover/reason:text-primary group-hover/reason:border-primary/20 transition-colors">
                                                        <Quote className="h-3 w-3 rotate-180" />
                                                        <span>{t('ui.citations', { count: highlights.length > 0 ? linked.length : chunkIds.length })}</span>
                                                    </div>
                                                )}
                                            </div>

                                            <p className="text-sm text-slate-600 pl-7 leading-relaxed mb-1">
                                                {reason.explanation}
                                            </p>


                                        </div>
                                    </div>
                                )
                            })}
                        </div>

                        {/* 评分判定理由 - 优化为专家评语风格 */}
                        <div className="bg-slate-50 rounded-lg p-4 border border-slate-200/60 mt-2 relative overflow-hidden group/justify">
                            <div className="flex items-center gap-2 mb-2 text-slate-500">
                                <Scale className="h-4 w-4" />
                                <span className="text-xs font-bold uppercase tracking-wider">{t('reports.scoring.justification')}</span>
                            </div>
                            <div className="relative pl-6">
                                <span className="absolute left-0 top-0 bottom-0 w-0.5 bg-slate-300/50 rounded-full" />
                                <p className="text-sm text-slate-700 leading-relaxed font-serif italic selection:bg-slate-200">
                                    {structuredReasoning.score_justification}
                                </p>
                            </div>
                        </div>
                    </div>
                )}

                {/* 引用原句 - 每句一条，按摘录顺序；点击高亮该句在原文中的全部区域 */}
                {citations && citations.length > 0 && (
                    <div className="bg-white/40 rounded-lg p-3 border border-border/30">
                        <h4 className="text-[11px] font-bold text-muted-foreground/60 uppercase tracking-wider mb-2 flex items-center gap-1.5 pl-1">
                            <Quote className="h-3 w-3 rotate-180" />
                            {t('reports.detail.quotedEvidence')}
                        </h4>
                        <div className="space-y-1">
                            {citations.map((citation, idx) => {
                                const page = citationPage(citation)
                                return (
                                    <button
                                        key={idx}
                                        type="button"
                                        disabled={page === null}
                                        onClick={() => onLocate(citationHighlights(citation), citation.text)}
                                        className={cn(
                                            "w-full flex items-start gap-3 px-3 py-2 text-left text-xs rounded-md transition-colors",
                                            page !== null
                                                ? "text-slate-600 hover:bg-white hover:text-slate-800 cursor-pointer"
                                                : "text-muted-foreground/70 cursor-default"
                                        )}
                                    >
                                        <span className="flex-1 leading-relaxed">{citation.text}</span>
                                        <span className={cn(
                                            "shrink-0 font-mono text-[10px] pt-0.5 whitespace-nowrap",
                                            page !== null ? "text-primary/70" : "text-muted-foreground/50"
                                        )}>
                                            {page !== null
                                                ? t('reports.detail.citationPage', { page })
                                                : t('reports.detail.citationPageUnknown')}
                                        </span>
                                    </button>
                                )
                            })}
                        </div>
                    </div>
                )}

                {/* TODO [COMPAT] 旧格式推理 - 兼容旧数据，后续清理 */}
                {legacyReasoning && (
                    <div>
                        <h4 className="text-xs font-medium text-muted-foreground mb-1">
                            {t('reports.detail.scoringReason')}
                        </h4>
                        <p className="text-sm bg-white/60 p-3 rounded border border-border/30">
                            {legacyReasoning}
                        </p>
                    </div>
                )}

                {/* 评价时间 */}
                {item.evaluated_at && (
                    <p className="text-xs text-muted-foreground/50 text-right">
                        {t('reports.detail.evaluatedAt')} {formatDate(item.evaluated_at, i18n.language)}
                    </p>
                )}
            </div>
        </details>
    )
}


export function Reports() {
    const { t, i18n } = useTranslation()
    const [reports, setReports] = useState<ReportSummary[]>([])
    const [folders, setFolders] = useState<BatchFolderInfo[]>([])
    const [currentFolder, setCurrentFolder] = useState<string | null>(null)
    const [loading, setLoading] = useState(true)
    const [selectedReport, setSelectedReport] = useState<ReportDetail | null>(null)
    const [loadingDetail, setLoadingDetail] = useState(false)
    const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid')
    const [detailTab, setDetailTab] = useState<'score' | 'evidence'>('score')
    // const [exporting, setExporting] = useState(false)
    const reportContentRef = useRef<HTMLDivElement>(null)

    // 原文查看抽屉状态
    const [sourceDrawerOpen, setSourceDrawerOpen] = useState(false)
    const [sourceDrawerChunkIds, setSourceDrawerChunkIds] = useState<string[]>([])
    const [sourceDrawerHighlights, setSourceDrawerHighlights] = useState<EvidenceMatch[] | undefined>(undefined)
    const [sourceDrawerReasonPoint, setSourceDrawerReasonPoint] = useState<string>('')
    const [sourceDrawerReasonExplanation, setSourceDrawerReasonExplanation] = useState<string>('')

    // 加载报告列表
    const loadReports = useCallback(async (folder?: string | null) => {
        setLoading(true)
        try {
            const result = await api.listReports(folder || undefined)
            setReports(result.reports)
            setFolders(result.folders)
        } catch (error) {
            console.error('加载报告列表失败:', error)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        loadReports(currentFolder)
    }, [loadReports, currentFolder, i18n.language])

    // Language changes refresh standard display fields without resetting the selected tab.
    const selectedTaskId = selectedReport?.task_id
    useEffect(() => {
        if (!selectedTaskId) return
        let active = true
        api.getReport(selectedTaskId, currentFolder || undefined)
            .then(detail => { if (active) setSelectedReport(detail) })
            .catch(error => { if (active) notifyError(error, 'reports.error.loadFailed') })
        return () => { active = false }
    }, [selectedTaskId, currentFolder, i18n.language])

    // 查看报告详情
    const viewReport = async (taskId: string) => {
        setLoadingDetail(true)
        try {
            const detail = await api.getReport(taskId, currentFolder || undefined)
            setSelectedReport(detail)
        } catch (error) {
            console.error('加载报告详情失败:', error)
        } finally {
            setLoadingDetail(false)
        }
    }

    // 返回列表
    const backToList = () => {
        setSelectedReport(null)
    }

    // 获取推荐图标
    const getRecommendationIcon = (recommendation: string | null) => {
        if (!recommendation) return <AlertCircle className="h-5 w-5 text-gray-400" />
        if (recommendation.toLowerCase().includes('not')) {
            return <XCircle className="h-5 w-5 text-red-500" />
        }
        return <CheckCircle2 className="h-5 w-5 text-green-500" />
    }

    // Determine status color helper - 基于质量评分 (1-7)
    const getStatusColor = (quality_score: number | null | undefined) => {
        if (quality_score === null || quality_score === undefined) return "text-gray-400 bg-gray-100"
        if (quality_score >= 5) return "text-emerald-600 bg-emerald-50"  // 5-7: 优秀
        if (quality_score >= 3) return "text-amber-600 bg-amber-50"  // 3-4: 中等
        return "text-red-600 bg-red-50"  // 1-2: 较差
    }

    /* 导出 PDF (暂不使用)
    const handleExportPdf = async () => {
        if (!reportContentRef.current || !selectedReport) return

        setExporting(true)
        try {
            const filename = `${selectedReport.task_id}_${selectedReport.guideline_title}`
            await exportToPdf(reportContentRef.current, filename)
        } catch (e) {
            console.error('导出 PDF 失败:', e)
        } finally {
            setExporting(false)
        }
    }
    */

    // 报告详情视图
    if (selectedReport) {
        return (
            <div className="p-8 max-w-6xl mx-auto space-y-6">
                {/* 返回按钮 */}
                <Button variant="ghost" onClick={backToList} className="gap-2">
                    <ArrowLeft className="h-4 w-4" />
                    {t('reports.backToList')}
                </Button>

                {/* 报告头部 - 液态玻璃风格 */}
                {/* 报告头部 - 优化版 (修复 Tooltip 遮挡，统一视觉) */}
                <div className="relative rounded-2xl border border-border/40 bg-white/40 shadow-sm hover:shadow-md transition-all duration-500">
                    {/* 背景装饰层 - 独立裁剪，确保背景不溢出但 Tooltip 可溢出 */}
                    <div className="absolute inset-0 overflow-hidden rounded-2xl -z-10">
                        <div className="absolute -top-24 -right-24 w-96 h-96 bg-primary/5 rounded-full blur-[80px] opacity-60" />
                        <div className="absolute -bottom-24 -left-24 w-80 h-80 bg-teal-500/5 rounded-full blur-[80px] opacity-60" />
                        <div className="absolute inset-0 bg-white/40 backdrop-blur-[2px]" />
                    </div>

                    {/* 内容层 - 无裁剪 */}
                    <div className="p-6 flex flex-col xl:flex-row items-start justify-between gap-6">
                        {/* 左侧信息 */}
                        {/* 左侧信息 */}
                        <div className="space-y-4 flex-1 min-w-0">
                            <div className="flex items-center gap-3">
                                <span className="px-2 py-0.5 rounded-md bg-white border border-border/50 text-xs font-mono text-muted-foreground shadow-sm">
                                    {selectedReport.task_id}
                                </span>
                            </div>
                            <div>
                                <h1 className="text-2xl font-bold tracking-tight text-gray-900 leading-tight">
                                    {selectedReport.guideline_title}
                                </h1>
                                {selectedReport.status === 'partial' && (
                                    <p className="mt-2 text-sm text-amber-700">
                                        {t('reports.partial', { completed: 23 - selectedReport.missing_items.length, missing: selectedReport.missing_items.join(', ') })}
                                    </p>
                                )}
                                <div className="flex items-center gap-4 text-sm text-gray-500 mt-2">
                                    <span className="flex items-center gap-1.5">
                                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500/60"></span>
                                        {t('reports.detail.date')} <span className="font-medium text-gray-700">{formatDate(selectedReport.evaluation_date, i18n.language, { dateStyle: 'medium' })}</span>
                                    </span>
                                </div>
                            </div>
                        </div>

                        {/* 右侧评分条 - 统一面板设计 */}
                        <div className="flex flex-shrink-0 items-stretch gap-2 xl:gap-4 bg-white/60 border border-white/60 shadow-sm rounded-xl backdrop-blur-sm overflow-visible p-1">
                            {/* 1. 整体评价 */}
                            {selectedReport.overall_quality_score && (
                                <div className="group relative px-5 py-3 flex flex-col justify-between min-w-[130px] rounded-lg hover:bg-white/50 transition-colors cursor-help">
                                    <div className="flex items-center justify-between mb-1 gap-2">
                                        <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider whitespace-nowrap">{t('reports.detail.qualityScore')}</span>
                                        <Info className="h-3 w-3 text-muted-foreground/50 group-hover:text-primary transition-colors" />
                                    </div>
                                    <div className="flex items-baseline gap-1">
                                        <span className="text-2xl font-black text-gray-900 leading-none">
                                            {selectedReport.overall_quality_score}
                                        </span>
                                        <span className="text-xs font-medium text-muted-foreground">/7</span>
                                    </div>

                                    {/* Tooltip */}
                                    <div className="absolute top-full right-0 xl:left-0 xl:right-auto mt-2 w-72 p-0 bg-white rounded-xl shadow-xl border border-border/60 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-200 z-50 ring-1 ring-black/5">
                                        <div className="p-3 border-b border-border/50 bg-gray-50/50 rounded-t-xl">
                                            <h4 className="font-semibold text-xs text-gray-900 flex items-center gap-2">
                                                <FileBarChart className="h-3.5 w-3.5 text-primary" />
                                                {t('reports.tooltip.qualityScoreTitle')}
                                            </h4>
                                        </div>
                                        <div className="p-2 grid grid-cols-1 gap-1">
                                            {[
                                                { r: "1-23", s: 1, c: "text-red-600 bg-red-50 border-red-100" },
                                                { r: "24-46", s: 2, c: "text-red-500 bg-red-50 border-red-100" },
                                                { r: "47-69", s: 3, c: "text-orange-600 bg-orange-50 border-orange-100" },
                                                { r: "70-92", s: 4, c: "text-amber-600 bg-amber-50 border-amber-100" },
                                                { r: "93-115", s: 5, c: "text-yellow-600 bg-yellow-50 border-yellow-100" },
                                                { r: "116-138", s: 6, c: "text-green-600 bg-green-50 border-green-100" },
                                                { r: "139-161", s: 7, c: "text-emerald-600 bg-emerald-50 border-emerald-100" }
                                            ].map((item, i) => (
                                                <div key={i} className="flex items-center justify-between px-2 py-1.5 rounded-lg hover:bg-muted/50 transition-colors">
                                                    <span className="font-mono text-[10px] text-muted-foreground/80">{item.r}</span>
                                                    <span className={cn("text-[10px] font-bold px-1.5 py-0.5 rounded border leading-none", item.c)}>{item.s}</span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* 2. 总分 */}
                            {selectedReport.total_raw_score && (
                                <div className="px-5 py-3 flex flex-col justify-between min-w-[100px] rounded-lg hover:bg-white/50 transition-colors">
                                    <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider whitespace-nowrap">{t('reports.detail.totalScore')}</span>
                                    <div className="flex items-baseline gap-0.5">
                                        <span className="text-xl font-bold text-gray-900 font-mono leading-none">
                                            {selectedReport.total_raw_score}
                                        </span>
                                        <span className="text-[10px] text-muted-foreground">{t('ui.points')}</span>
                                    </div>
                                </div>
                            )}

                            {/* 3. 推荐结论 */}
                            <div className={cn(
                                "group relative px-5 py-3 flex flex-col justify-between min-w-[160px] cursor-help transition-colors rounded-lg",
                                selectedReport.overall_assessment.recommendation?.toLowerCase().includes('not')
                                    ? "bg-red-50/20 hover:bg-red-50/50"
                                    : selectedReport.overall_assessment.recommendation?.toLowerCase().includes('modification')
                                        ? "bg-amber-50/20 hover:bg-amber-50/50"
                                        : "bg-emerald-50/20 hover:bg-emerald-50/50"
                            )}>
                                <div className="flex items-center justify-between mb-1">
                                    <span className={cn(
                                        "text-xs font-semibold uppercase tracking-wider",
                                        selectedReport.overall_assessment.recommendation?.toLowerCase().includes('not') ? "text-red-600/70" :
                                            selectedReport.overall_assessment.recommendation?.toLowerCase().includes('modification') ? "text-amber-600/70" : "text-emerald-600/70"
                                    )}>{t('reports.table.recommendation')}</span>
                                    <Info className={cn(
                                        "h-3.5 w-3.5 transition-colors",
                                        selectedReport.overall_assessment.recommendation?.toLowerCase().includes('not') ? "text-red-300 group-hover:text-red-500" :
                                            selectedReport.overall_assessment.recommendation?.toLowerCase().includes('modification') ? "text-amber-300 group-hover:text-amber-500" : "text-emerald-300 group-hover:text-emerald-500"
                                    )} />
                                </div>
                                <div className="flex items-center gap-2">
                                    {getRecommendationIcon(selectedReport.overall_assessment.recommendation)}
                                    <span className={cn(
                                        "font-bold text-sm leading-tight",
                                        selectedReport.overall_assessment.recommendation?.toLowerCase().includes('not') ? "text-red-700" :
                                            selectedReport.overall_assessment.recommendation?.toLowerCase().includes('modification') ? "text-amber-700" : "text-emerald-700"
                                    )}>
                                        {selectedReport.overall_assessment.recommendation || t('reports.detail.notEvaluated')}
                                    </span>
                                </div>

                                {/* 推荐规则 Tooltip */}
                                <div className="absolute top-full right-0 mt-3 w-80 p-0 bg-white rounded-xl shadow-lg border border-border opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-200 z-50 ring-1 ring-black/5">
                                    <div className="p-3 border-b border-border/50 bg-gray-50/50 rounded-t-xl">
                                        <h4 className="font-semibold text-xs text-gray-900 flex items-center gap-2">
                                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                                            {t('reports.tooltip.recommendationTitle')}
                                        </h4>
                                    </div>
                                    <div className="p-3 space-y-2">
                                        <div className="relative pl-3 border-l-2 border-red-200">
                                            <div className="text-[10px] font-bold text-red-600 uppercase mb-0.5">{t('ui.noRecommend')}</div>
                                            <p className="text-[10px] text-muted-foreground leading-relaxed">{t('reports.tooltip.noRecommend')}</p>
                                        </div>
                                        <div className="relative pl-3 border-l-2 border-amber-200">
                                            <div className="text-[10px] font-bold text-amber-600 uppercase mb-0.5">{t('ui.modifications')}</div>
                                            <p className="text-[10px] text-muted-foreground leading-relaxed">{t('reports.tooltip.recommendModify')}</p>
                                        </div>
                                        <div className="relative pl-3 border-l-2 border-emerald-200">
                                            <div className="text-[10px] font-bold text-emerald-600 uppercase mb-0.5">{t('ui.recommend')}</div>
                                            <p className="text-[10px] text-muted-foreground leading-relaxed">{t('reports.tooltip.recommend')}</p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Tab 切换 */}
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 p-1 bg-muted rounded-xl w-fit">
                        <Button
                            variant={detailTab === 'score' ? 'default' : 'ghost'}
                            size="sm"
                            onClick={() => setDetailTab('score')}
                            className="gap-2"
                        >
                            <FileBarChart className="h-4 w-4" />
                            {t('reports.tab.score')}
                        </Button>
                        <Button
                            variant={detailTab === 'evidence' ? 'default' : 'ghost'}
                            size="sm"
                            onClick={() => setDetailTab('evidence')}
                            className="gap-2"
                        >
                            <Eye className="h-4 w-4" />
                            {t('reports.tab.evidence')}
                        </Button>
                    </div>
                    {/* 导出按钮 (暂不使用)
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={undefined}
                        disabled={true}
                        className="gap-2"
                    >
                        <_Download className="h-4 w-4" />
                        {t('reports.export.pdf')}
                    </Button>
                    */}
                </div>

                {/* Tab 内容 */}
                {
                    detailTab === 'evidence' ? (
                        <EvidenceViewer taskId={selectedReport.task_id} />
                    ) : (
                        <div ref={reportContentRef} className="space-y-6">
                            {/* 领域评分 */}
                            <div className="bg-card rounded-xl border p-6">
                                <h2 className="text-lg font-semibold mb-4">{t('reports.detail.domainScores')}</h2>
                                <div className="space-y-3">
                                    {Object.entries(selectedReport.domain_scores).map(([key, domain]) => (
                                        <div key={key} className="flex items-center gap-4">
                                            <span className="w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center text-sm font-medium">
                                                {key}
                                            </span>
                                            <span className="flex-1 text-sm">{domain.name}</span>
                                            <div className="w-48 bg-muted rounded-full h-2 overflow-hidden">
                                                {domain.score !== null ? (
                                                    <div
                                                        className="h-full bg-primary rounded-full transition-all"
                                                        style={{ width: `${domain.score}%` }}
                                                    />
                                                ) : (
                                                    <div className="h-full bg-muted-foreground/20 w-full" />
                                                )}
                                            </div>
                                            <span className={cn(
                                                "w-28 text-right text-sm font-medium",
                                                domain.score === null && "text-muted-foreground"
                                            )}>
                                                {domain.score !== null
                                                    ? `${domain.score}%`
                                                    : domain.evaluated_count > 0
                                                        ? t('reports.detail.pendingDomain', { completed: domain.evaluated_count, total: domain.item_count })
                                                        : t('reports.detail.notEvaluated')}
                                            </span>

                                        </div>
                                    ))}
                                </div>
                            </div>

                            {/* 总体评估 */}
                            {(selectedReport.overall_assessment.strengths?.length > 0 ||
                                selectedReport.overall_assessment.weaknesses?.length > 0) && (
                                    <div className="grid md:grid-cols-2 gap-4">
                                        {selectedReport.overall_assessment.strengths?.length > 0 && (
                                            <div className="bg-green-50 rounded-xl border border-green-100 p-6">
                                                <h3 className="font-medium text-green-800 mb-3">{t(selectedReport.status === 'partial' ? 'reports.detail.partialStrengths' : 'reports.detail.strengths')}</h3>
                                                <ul className="space-y-2">
                                                    {selectedReport.overall_assessment.strengths.map((s, i) => (
                                                        <li key={i} className="text-sm text-green-700">{s}</li>
                                                    ))}
                                                </ul>
                                            </div>
                                        )}
                                        {selectedReport.overall_assessment.weaknesses?.length > 0 && (
                                            <div className="bg-red-50 rounded-xl border border-red-100 p-6">
                                                <h3 className="font-medium text-red-800 mb-3">{t(selectedReport.status === 'partial' ? 'reports.detail.partialWeaknesses' : 'reports.detail.weaknesses')}</h3>
                                                <ul className="space-y-2">
                                                    {selectedReport.overall_assessment.weaknesses.map((w, i) => (
                                                        <li key={i} className="text-sm text-red-700">{w}</li>
                                                    ))}
                                                </ul>
                                            </div>
                                        )}
                                    </div>
                                )}

                            {/* 条目评价详情 */}
                            {selectedReport.item_evaluations?.length > 0 && (
                                <div className="bg-card rounded-xl border p-6">
                                    <h2 className="text-lg font-semibold mb-4">{t('reports.detail.itemDetails')}</h2>
                                    <div className="space-y-4">
                                        {[...selectedReport.item_evaluations]
                                            .sort((a, b) => {
                                                // 提取 item_id 中的数字进行排序
                                                const numA = parseInt(a.item_id.replace(/\D/g, '')) || 0
                                                const numB = parseInt(b.item_id.replace(/\D/g, '')) || 0
                                                return numA - numB
                                            })
                                            .map((item) => (
                                                <ItemEvaluationCard
                                                    key={item.item_id}
                                                    item={item}
                                                    onReasonClick={(chunkIds, point, explanation) => {
                                                        setSourceDrawerChunkIds(chunkIds)
                                                        setSourceDrawerHighlights(undefined)
                                                        setSourceDrawerReasonPoint(point)
                                                        setSourceDrawerReasonExplanation(explanation || '')
                                                        setSourceDrawerOpen(true)
                                                    }}
                                                    onLocate={(highlights, point, explanation) => {
                                                        setSourceDrawerHighlights(highlights)
                                                        setSourceDrawerReasonPoint(point)
                                                        setSourceDrawerReasonExplanation(explanation || '')
                                                        setSourceDrawerOpen(true)
                                                    }}
                                                />
                                            ))}
                                    </div>
                                </div>
                            )}

                            {/* 原文查看抽屉 */}
                            <SourceDrawer
                                isOpen={sourceDrawerOpen}
                                onClose={() => setSourceDrawerOpen(false)}
                                taskId={selectedReport.task_id}
                                folder={currentFolder}
                                chunkIds={sourceDrawerChunkIds}
                                highlights={sourceDrawerHighlights}
                                reasonPoint={sourceDrawerReasonPoint}
                                reasonExplanation={sourceDrawerReasonExplanation}
                            />
                        </div>
                    )
                }
            </div >
        )
    }

    // 报告列表视图
    return (
        <div className="h-full w-full overflow-y-auto bg-[#f5f5f7] p-8 space-y-10" style={{ overscrollBehavior: 'contain', willChange: 'scroll-position' }}>
            <header className="relative z-10 flex flex-col sm:flex-row sm:items-end justify-between gap-6 pb-6 border-b border-gray-200/50">
                <div className="space-y-2">
                    <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-black/36">
                        {t('ui.reportingSurface')}
                    </div>
                    <h2 className="text-4xl font-semibold tracking-[-0.045em] text-[#1d1d1f]">
                        {t('reports.title')}
                    </h2>
                    <p className="max-w-2xl text-[1.02rem] leading-[1.55] text-black/58">
                        {t('reports.description')}
                    </p>
                </div>

                <div className="flex items-center gap-3">
                    <div className="flex items-center p-1 bg-white rounded-xl border border-gray-200/60 shadow-sm">
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setViewMode('grid')}
                            className={cn(
                                "h-8 w-8 p-0 rounded-lg transition-all",
                                viewMode === 'grid'
                                    ? "bg-[#0071e3]/8 text-[#0071e3] shadow-sm"
                                    : "text-gray-400 hover:text-gray-600 hover:bg-gray-50"
                            )}
                        >
                            <LayoutGrid className="h-4 w-4" />
                        </Button>
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setViewMode('list')}
                            className={cn(
                                "h-8 w-8 p-0 rounded-lg transition-all",
                                viewMode === 'list'
                                    ? "bg-[#0071e3]/8 text-[#0071e3] shadow-sm"
                                    : "text-gray-400 hover:text-gray-600 hover:bg-gray-50"
                            )}
                        >
                            <List className="h-4 w-4" />
                        </Button>
                    </div>

                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => loadReports(currentFolder)}
                        disabled={loading}
                        className="h-10 gap-2 rounded-full border-black/10 bg-white px-4 text-gray-600 shadow-sm transition-all hover:border-[#0071e3]/20 hover:bg-[#0071e3]/6 hover:text-[#0071e3]"
                    >
                        <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
                        {t('reports.refresh')}
                    </Button>
                </div>
            </header>

            {/* 面包屑导航 */}
            {(currentFolder || folders.length > 0) && (
                <div className="flex items-center gap-2 text-sm mb-6 px-1">
                    <button
                        onClick={() => setCurrentFolder(null)}
                        className={cn(
                            "flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-all",
                            !currentFolder
                                ? "font-medium text-[#0071e3] bg-[#0071e3]/8"
                                : "text-gray-600 hover:text-[#0071e3] hover:bg-gray-50"
                        )}
                    >
                        <Home className="h-4 w-4" />
                        {t('reports.breadcrumb.root')}
                    </button>
                    {currentFolder && (
                        <>
                            <ChevronRight className="h-4 w-4 text-gray-400" />
                            <span className="flex items-center gap-1.5 rounded-lg bg-[#0071e3]/8 px-3 py-1.5 font-medium text-[#0071e3]">
                                <Folder className="h-4 w-4" />
                                {currentFolder}
                            </span>
                        </>
                    )}
                </div>
            )}

            {/* 文件夹卡片 */}
            {!currentFolder && folders.length > 0 && (
                <div className="grid gap-4 md:grid-cols-4 mb-6">
                    {folders.map(folder => (
                        <Card
                            key={folder.name}
                            onClick={() => setCurrentFolder(folder.name)}
                            className="cursor-pointer hover:shadow-lg hover:-translate-y-1 transition-all duration-300 border-gray-200 hover:border-amber-300 bg-white group"
                        >
                            <div className="p-4 flex items-center gap-3">
                                <div className="p-2.5 rounded-xl bg-amber-50 text-amber-600 group-hover:bg-amber-100 transition-colors">
                                    <Folder className="h-6 w-6" />
                                </div>
                                <div>
                                    <h3 className="font-semibold text-gray-900 group-hover:text-amber-700 transition-colors">{folder.name}</h3>
                                    <p className="text-xs text-gray-500">
                                        {t('reports.folder.count', { count: folder.report_count })}
                                    </p>
                                </div>
                            </div>
                        </Card>
                    ))}
                </div>
            )}

            {loading ? (
                <div className="flex flex-col items-center justify-center py-32 text-gray-400">
                    <Loader2 className="h-8 w-8 animate-spin mb-3 text-primary" />
                    <span className="text-sm font-medium">{t('common.loadingData')}</span>
                </div>
            ) : reports.length === 0 && folders.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-32 text-gray-400 border border-dashed border-gray-200 rounded-xl bg-gray-50/50">
                    <FileText className="h-16 w-16 mb-4 opacity-20" />
                    <p className="text-lg font-medium text-gray-900">{t('reports.empty.title')}</p>
                    <p className="text-sm mt-2">{t('reports.empty.hint')}</p>
                </div>
            ) : reports.length === 0 && currentFolder ? (
                <div className="flex flex-col items-center justify-center py-32 text-gray-400 border border-dashed border-gray-200 rounded-xl bg-gray-50/50">
                    <Folder className="h-16 w-16 mb-4 opacity-20" />
                    <p className="text-lg font-medium text-gray-900">{t('reports.empty.folder')}</p>
                </div>
            ) : (
                <>
                    {viewMode === 'list' ? (
                        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden shadow-sm">
                            <table className="w-full text-sm text-left">
                                <thead className="bg-gray-50/50 border-b border-gray-100 text-gray-500">
                                    <tr>
                                        <th className="px-6 py-4 font-medium w-16">{t('reports.table.status')}</th>
                                        <th className="px-6 py-4 font-medium w-48">{t('reports.table.taskId')}</th>
                                        <th className="px-6 py-4 font-medium">{t('reports.table.title')}</th>
                                        <th className="px-6 py-4 font-medium w-32">{t('reports.table.time')}</th>
                                        <th className="px-6 py-4 font-medium w-32 text-center">{t('reports.table.score')}</th>
                                        <th className="px-6 py-4 font-medium w-40 text-center">{t('reports.table.recommendation')}</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100">
                                    {reports.map((report) => (
                                        <tr
                                            key={report.task_id}
                                            className="group hover:bg-gray-50/80 transition-colors cursor-pointer"
                                            onClick={() => viewReport(report.task_id)}
                                        >
                                            <td className="px-6 py-4">
                                                <div className={cn("w-8 h-8 rounded-lg flex items-center justify-center", getStatusColor(report.quality_score))}>
                                                    <FileText className="h-4 w-4" />
                                                </div>
                                            </td>
                                            <td className="px-6 py-4">
                                                <div className="font-mono text-xs text-gray-500 whitespace-nowrap">{report.task_id}</div>
                                            </td>
                                            <td className="px-6 py-4">
                                                <div className="font-medium text-gray-900 line-clamp-2">{report.title}</div>
                                                {report.status === 'partial' && <div className="text-xs text-amber-700">{t('reports.partial', { completed: 23 - report.missing_items.length, missing: report.missing_items.join(', ') })}</div>}
                                            </td>
                                            <td className="px-6 py-4">
                                                <div className="text-gray-900 whitespace-nowrap">{formatDate(report.date, i18n.language, { dateStyle: 'medium' })}</div>
                                                {report.date.split(' ')[1] && (
                                                    <div className="text-xs text-gray-400 whitespace-nowrap font-mono mt-0.5">
                                                        {formatDate(report.date, i18n.language, { timeStyle: 'short' })}
                                                    </div>
                                                )}
                                            </td>
                                            <td className="px-6 py-4 text-center">
                                                <span className={cn(
                                                    "text-lg font-bold font-mono",
                                                    report.quality_score && report.quality_score >= 5 ? "text-emerald-600" :
                                                        report.quality_score && report.quality_score >= 3 ? "text-amber-600" : "text-red-600",
                                                    !report.quality_score && "text-gray-400"
                                                )}>
                                                    {report.quality_score !== null ? `${report.quality_score}/7` : '-'}
                                                </span>
                                            </td>
                                            <td className="px-6 py-4 text-center">
                                                {report.recommendation ? (
                                                    <div className="flex items-center justify-center gap-1.5 whitespace-nowrap">
                                                        {getRecommendationIcon(report.recommendation)}
                                                        <span className="text-gray-700">{report.recommendation}</span>
                                                    </div>
                                                ) : (
                                                    <span className="text-gray-400 text-xs">{t('reports.noConclusion')}</span>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
                            {reports.map((report) => (
                                <Card
                                    key={report.task_id}
                                    className="group cursor-pointer hover:shadow-lg hover:-translate-y-1 transition-all duration-300 border-gray-200 hover:border-primary/20 bg-white"
                                    onClick={() => viewReport(report.task_id)}
                                >
                                    <div className="p-5 space-y-4">
                                        <div className="flex items-start justify-between">
                                            <div className={cn("p-2.5 rounded-xl", getStatusColor(report.quality_score))}>
                                                <FileText className="h-5 w-5" />
                                            </div>
                                            {report.quality_score !== null ? (
                                                <div className="flex items-baseline">
                                                    <span className={cn(
                                                        "text-2xl font-bold font-mono",
                                                        report.quality_score >= 5 ? "text-emerald-600" :
                                                            report.quality_score >= 3 ? "text-amber-600" : "text-red-600"
                                                    )}>
                                                        {report.quality_score}
                                                    </span>
                                                    <span className="text-sm text-gray-400 ml-1">/7</span>
                                                </div>
                                            ) : (
                                                <span className="text-gray-300 font-mono">{t('ui.notAvailable')}</span>
                                            )}
                                        </div>

                                        <div>
                                            <h3 className="font-semibold text-gray-900 line-clamp-1 mb-1 group-hover:text-primary transition-colors">{report.title}</h3>
                                            <p className="text-xs font-mono text-gray-400">{report.task_id}</p>
                                        </div>

                                        <div className="pt-4 border-t border-gray-50 flex items-center justify-between text-sm">
                                            <span className="text-gray-500">{formatDate(report.date, i18n.language)}</span>
                                            {report.recommendation && (
                                                <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-gray-50 group-hover:bg-white transition-colors">
                                                    {getRecommendationIcon(report.recommendation)}
                                                    <span className="text-xs font-medium text-gray-600">{report.recommendation}</span>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </Card>
                            ))}
                        </div>
                    )}
                </>
            )}

            {loadingDetail && (
                <div className="fixed inset-0 bg-black/20 flex items-center justify-center z-50">
                    <div className="flex flex-col items-center gap-3">
                        <Loader2 className="h-8 w-8 animate-spin text-primary" />
                        <span className="text-sm text-muted-foreground">{t('reports.loading.detail')}</span>
                    </div>
                </div>
            )}
        </div>
    )
}
