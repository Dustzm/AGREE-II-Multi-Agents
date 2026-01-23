import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronUp, Download, BarChart3, Trophy, Target, Activity, LayoutGrid, Save, Edit, Loader2 } from 'lucide-react'
import { BenchmarkSummary, GuideConsistency, ItemConsistency, ScoreUpdate } from '@/services/api'
import { api } from '@/services/api'
import { toast } from 'react-hot-toast'

interface Props {
    summary: BenchmarkSummary
    batchIds: string[]
    xlsxFile: string
}

// Tab 类型：0 表示汇总（第一个sheet），1-N 表示各指南详情
type ActiveTab = number

// 内部状态：记录修改的分数
// Key格式: "${guideId}_${itemId}_${field}"
type ModifiedScores = Record<string, number>

// AGREE II 领域定义
const DOMAINS = [
    { id: 1, key: "scope", items: [1, 2, 3] },
    { id: 2, key: "stakeholder", items: [4, 5, 6] },
    { id: 3, key: "rigour", items: [7, 8, 9, 10, 11, 12, 13, 14] },
    { id: 4, key: "clarity", items: [15, 16, 17] },
    { id: 5, key: "applicability", items: [18, 19, 20, 21] },
    { id: 6, key: "independence", items: [22, 23] },
] as const

// 获取一致性颜色
function getConsistencyColor(value: number): string {
    if (value >= 0.8) return 'from-emerald-400 to-emerald-500'
    if (value >= 0.6) return 'from-amber-400 to-amber-500'
    return 'from-rose-400 to-rose-500'
}

function getConsistencyTextColor(value: number): string {
    if (value >= 0.8) return 'text-emerald-600'
    if (value >= 0.6) return 'text-amber-600'
    return 'text-rose-600'
}

function getConsistencyLightBg(value: number): string {
    if (value >= 0.8) return 'bg-emerald-50'
    if (value >= 0.6) return 'bg-amber-50'
    return 'bg-rose-50'
}

// 可编辑分数组件
function EditableScore({ 
    value, 
    originalValue, 
    onChange, 
    isModel = false 
}: { 
    value: number | null | undefined; 
    originalValue: number | null | undefined; 
    onChange: (newValue: number) => void;
    isModel?: boolean;
}) {
    const [isEditing, setIsEditing] = useState(false)
    const [tempValue, setTempValue] = useState('')

    // 初始化/更新 tempValue
    useEffect(() => {
        setTempValue(value?.toString() || '')
    }, [value])

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
            // Invalid input, revert
            setTempValue(value?.toString() || '')
        }
        setIsEditing(false)
    }

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
            handleSave()
        } else if (e.key === 'Escape') {
            setTempValue(value?.toString() || '')
            setIsEditing(false)
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
                className="w-12 px-1 py-0.5 text-center text-sm font-mono border-2 border-violet-400 rounded focus:outline-none focus:ring-2 focus:ring-violet-200"
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
                ${isModel ? 'text-violet-700' : 'text-slate-700'}
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

// 总览统计卡片 - 优化版
function StatCard({ label, value, subValue, icon: Icon, colorClass }: { label: string; value: string; subValue?: string; icon?: any; colorClass?: string }) {
    return (
        <div className="relative overflow-hidden bg-white/40 backdrop-blur-md rounded-2xl p-4 border border-white/50 shadow-sm transition-all hover:shadow-md group flex-1">
            <div className="flex items-start justify-between">
                <div>
                    <p className="text-xs font-medium text-slate-500 mb-1">{label}</p>
                    <p className={`text-2xl font-black tracking-tight ${colorClass || 'text-slate-700'}`}>
                        {value}
                    </p>
                    {subValue && <p className="text-[10px] text-slate-400 mt-1 font-medium">{subValue}</p>}
                </div>
                {Icon && (
                    <div className={`p-2 rounded-xl bg-white/60 ${colorClass ? colorClass.replace('text-', 'bg-').replace('600', '100').replace('700', '100') : 'bg-slate-100'} bg-opacity-50`}>
                        <Icon className={`w-5 h-5 ${colorClass || 'text-slate-500'}`} />
                    </div>
                )}
            </div>
        </div>
    )
}

// 环形进度指示器 - 优化版
function ConsistencyRing({ value }: { value: number }) {
    const percentage = Math.round(value * 100)
    const circumference = 2 * Math.PI * 42
    const strokeDashoffset = circumference - (value * circumference)

    return (
        <div className="relative w-40 h-40 flex items-center justify-center">
            {/* 背景光晕 */}
            <div className="absolute inset-0 bg-violet-500/20 rounded-full blur-2xl animate-pulse-slow" />
            
            <svg className="w-full h-full transform -rotate-90 relative z-10" viewBox="0 0 100 100">
                {/* 轨道 */}
                <circle
                    cx="50" cy="50" r="42"
                    stroke="currentColor"
                    strokeWidth="8"
                    fill="none"
                    className="text-slate-100/50"
                />
                {/* 进度条 */}
                <circle
                    cx="50" cy="50" r="42"
                    stroke="url(#gradient-ring)"
                    strokeWidth="8"
                    fill="none"
                    strokeLinecap="round"
                    style={{
                        strokeDasharray: circumference,
                        strokeDashoffset: strokeDashoffset,
                        transition: 'stroke-dashoffset 1.5s cubic-bezier(0.4, 0, 0.2, 1)'
                    }}
                />
                <defs>
                    <linearGradient id="gradient-ring" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor="#8B5CF6" />
                        <stop offset="50%" stopColor="#6366F1" />
                        <stop offset="100%" stopColor="#EC4899" />
                    </linearGradient>
                </defs>
            </svg>
            
            {/* 中间文字 */}
            <div className="absolute inset-0 flex flex-col items-center justify-center z-20">
                <span className="text-4xl font-black text-slate-800 tracking-tighter drop-shadow-sm">{percentage}%</span>
                <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mt-1">一致性</span>
            </div>
        </div>
    )
}

// 指南一致性排行榜
function GuideBarChart({ guides }: { guides: GuideConsistency[] }) {
    const sorted = [...guides].sort((a, b) => b.consistency - a.consistency)

    if (sorted.length === 0) return <div className="text-center text-gray-400 py-8 text-sm">暂无数据</div>

    return (
        <div className="space-y-3 max-h-[500px] overflow-y-auto pr-2 custom-scrollbar">
            {sorted.map((guide, idx) => {
                const isTop3 = idx < 3
                return (
                    <div key={guide.guide_id} className="group relative">
                        <div className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-white/40 border border-transparent hover:border-violet-200 hover:bg-white/70 transition-all duration-300">
                            {/* 排名徽章 */}
                            <div className={`
                                w-6 h-6 flex items-center justify-center rounded-lg text-[10px] font-bold flex-shrink-0
                                ${idx === 0 ? 'bg-yellow-100 text-yellow-700 shadow-sm' :
                                  idx === 1 ? 'bg-slate-100 text-slate-600 shadow-sm' :
                                  idx === 2 ? 'bg-orange-100 text-orange-700 shadow-sm' :
                                  'bg-transparent text-slate-400'}
                            `}>
                                {idx + 1}
                            </div>
                            
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center justify-between mb-1.5">
                                    <div className="flex items-center gap-2 min-w-0">
                                        <span className="text-xs font-medium text-slate-500 truncate" title={guide.guide_name || guide.guide_id}>
                                            指南{guide.seq_no}
                                        </span>
                                        <span className="text-xs font-mono text-slate-700 truncate">
                                            {guide.guide_id}
                                        </span>
                                    </div>
                                    <span className={`text-xs font-bold font-mono ${getConsistencyTextColor(guide.consistency)} flex-shrink-0`}>
                                        {(guide.consistency * 100).toFixed(1)}%
                                    </span>
                                </div>
                                {/* 进度条背景 */}
                                <div className="h-1.5 bg-slate-100/80 rounded-full overflow-hidden">
                                    <div
                                        className={`h-full bg-gradient-to-r ${getConsistencyColor(guide.consistency)} rounded-full transition-all duration-1000 ease-out`}
                                        style={{ width: `${guide.consistency * 100}%` }}
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                )
            })}
        </div>
    )
}

// 条目一致性热力图（按领域分组）
function DomainItemHeatmap({ items }: { items: ItemConsistency[] }) {
    const { t } = useTranslation()

    const itemMap = new Map(items.map(i => {
        const id = parseInt(i.item_id.replace('item_', ''))
        return [id, i]
    }))

    return (
        <div className="grid grid-cols-3 gap-4 content-start">
            {DOMAINS.map((domain) => (
                <div key={domain.id} className="bg-white/40 border border-white/50 rounded-xl p-3.5 hover:bg-white/60 transition-colors shadow-sm">
                    <h4 className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-3 flex items-center justify-between border-b border-slate-100/50 pb-2">
                        <span className="flex items-center gap-1.5">
                            <span className="w-1.5 h-1.5 rounded-full bg-violet-400"></span>
                            D{domain.id}
                        </span>
                        <span className="text-slate-400 font-medium">
                            {t(`benchmark.visualization.domains.${domain.key}`)}
                        </span>
                    </h4>
                    <div className="flex flex-wrap gap-2">
                        {domain.items.map((itemId) => {
                            const item = itemMap.get(itemId)
                            if (!item) return null

                            const consistency = item.consistency

                            return (
                                <div
                                    key={itemId}
                                    className={`
                                        group relative flex flex-col items-center justify-center
                                        w-11 h-11 rounded-lg border border-transparent
                                        transition-all duration-200 cursor-default
                                        ${getConsistencyLightBg(consistency)}
                                        hover:scale-110 hover:shadow-md hover:z-10
                                    `}
                                    title={`${item.item_name}: ${(consistency * 100).toFixed(0)}%`}
                                >
                                    <span className={`text-[10px] font-bold opacity-60 ${getConsistencyTextColor(consistency)}`}>
                                        #{itemId}
                                    </span>
                                    <span className={`text-[11px] font-black ${getConsistencyTextColor(consistency)} leading-none`}>
                                        {(consistency * 100).toFixed(0)}
                                    </span>
                                </div>
                            )
                        })}
                    </div>
                </div>
            ))}
        </div>
    )
}

export function GuideDetailsPanel({
    summary,
    batchIds,
    xlsxFile
}: Props) {
    const { t } = useTranslation()
    const [isExpanded, setIsExpanded] = useState(true)
    const [activeTab, setActiveTab] = useState<ActiveTab>(0)
    const [modifiedScores, setModifiedScores] = useState<ModifiedScores>({})
    const [isSaving, setIsSaving] = useState(false)

    const details = summary.guide_details || []
    if (!details || details.length === 0) return null

    // 辅助函数：生成 Key
    const getScoreKey = (guideId: string, itemId: string, field: string) => `${guideId}::${itemId}::${field}`

    // 辅助函数：获取当前分数（修改值或原始值）
    const getCurrentScore = (guideId: string, itemId: string, field: string, original: number | null | undefined) => {
        const key = getScoreKey(guideId, itemId, field)
        return modifiedScores[key] ?? original
    }

    // 处理分数变更
    const handleScoreChange = (guideId: string, itemId: string, field: string, newValue: number) => {
        const key = getScoreKey(guideId, itemId, field)
        setModifiedScores(prev => ({
            ...prev,
            [key]: newValue
        }))
    }

    // 保存更改
    const handleSaveChanges = async (e: React.MouseEvent) => {
        e.stopPropagation()
        if (Object.keys(modifiedScores).length === 0) return

        setIsSaving(true)
        const toastId = toast.loading('正在保存更改...')

        try {
            const updates: ScoreUpdate[] = Object.entries(modifiedScores).map(([key, value]) => {
                const [guideId, itemId, field] = key.split('::')
                return { guideId, itemId, field, value }
            })

            await api.saveBenchmarkChanges(xlsxFile, updates)
            
            toast.success('保存成功', { id: toastId })
            // 清空修改状态（或者应该重新加载数据？理想情况下重新加载，这里简单处理先保留，
            // 但如果后端保存成功，下次渲染应该用新数据。由于我们没有SWR，
            // 且modifiedScores是本地状态，如果不清空，它会继续覆盖原始值。
            // 实际上应该触发父组件重新加载数据。
            // 但为了简化，我们可以不清空，让用户继续编辑。
            // 可是原始值没变，highlight会一直存在。
            // 最好是重新加载页面或数据。
            // 简单起见：刷新页面
            window.location.reload()
        } catch (error) {
            toast.error('保存失败: ' + (error as Error).message, { id: toastId })
        } finally {
            setIsSaving(false)
        }
    }

    const hasChanges = Object.keys(modifiedScores).length > 0

    // 构建标签数据：包含短标签（显示用）、完整名称（tooltip用）和指南ID
    const tabs = [
        { id: 0, shortLabel: t('benchmark.details.consistency'), fullName: t('benchmark.details.consistency'), guideId: undefined, type: 'summary', hasChanges: false },
        ...details.map((d, i) => ({
            id: i + 1,
            shortLabel: `指南${d.seq_no}`,
            fullName: d.guide_name || d.guide_id,
            guideId: d.guide_id,
            type: 'guide',
            hasChanges: Object.keys(modifiedScores).some(k => k.startsWith(`${d.guide_id}::`))
        }))
    ]

    return (
        <div className="mt-6 bg-white/60 backdrop-blur-xl rounded-3xl border border-white/70 shadow-xl shadow-gray-200/50 overflow-hidden">
            {/* 折叠/展开 头部 */}
            <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="w-full px-6 py-4 flex items-center justify-between hover:bg-white/40 transition-colors"
            >
                <div className="flex items-center gap-3">
                    <div className="p-2 rounded-xl bg-gradient-to-br from-violet-500/10 to-purple-500/10">
                        <BarChart3 className="w-5 h-5 text-violet-600" />
                    </div>
                    <div>
                        <span className="font-semibold text-gray-700">
                            {t('benchmark.details.title')}
                        </span>
                        <span className="ml-2 text-xs text-gray-400 bg-gray-100/80 px-2 py-0.5 rounded-full">
                            {details.length} {t('benchmark.details.guides')}
                        </span>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {hasChanges && (
                        <button
                            onClick={handleSaveChanges}
                            disabled={isSaving}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-violet-600 text-white hover:bg-violet-700 transition-all font-medium text-sm shadow-sm disabled:opacity-50 disabled:cursor-not-allowed animate-in fade-in slide-in-from-right-4"
                        >
                            {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                            保存更改
                        </button>
                    )}
                    <button
                        onClick={(e) => {
                            e.stopPropagation()
                            window.open(api.getBenchmarkDownloadUrl(xlsxFile), '_blank')
                        }}
                        className="p-2 rounded-xl hover:bg-white/60 text-gray-400 hover:text-violet-600 transition-all"
                        title={t('benchmark.run.download')}
                    >
                        <Download className="w-4 h-4" />
                    </button>
                    {isExpanded ? <ChevronUp className="w-5 h-5 text-gray-400" /> : <ChevronDown className="w-5 h-5 text-gray-400" />}
                </div>
            </button>

            {/* 展开内容 - 左右布局 */}
            {isExpanded && (
                <div className="flex flex-col lg:flex-row border-t border-gray-100/50 items-stretch">
                    {/* 左侧导航 */}
                    <div className="w-full lg:w-56 border-b lg:border-b-0 lg:border-r border-gray-100/50 bg-slate-50/30 overflow-y-auto custom-scrollbar flex-shrink-0">
                        <div className="p-3 space-y-1">
                            {tabs.map((tab, idx) => {
                                const isActive = activeTab === idx
                                return (
                                    <button
                                        key={tab.id}
                                        onClick={() => setActiveTab(idx as ActiveTab)}
                                        className={`
                                            relative w-full text-left px-4 py-3 text-sm font-medium transition-all duration-300
                                            flex flex-col items-start gap-0.5 rounded-xl border
                                            group
                                            ${isActive
                                                ? 'bg-white/80 backdrop-blur-md shadow-[0_4px_20px_-4px_rgba(139,92,246,0.1)] border-white/60 text-violet-700'
                                                : 'border-transparent text-slate-500 hover:bg-white/40 hover:text-slate-700'
                                            }
                                        `}
                                        title={tab.fullName}
                                    >
                                        {/* 选中态的左侧光晕指示条 */}
                                        {isActive && (
                                            <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-8 bg-gradient-to-b from-violet-400 to-indigo-400 rounded-r-full shadow-[0_0_12px_rgba(139,92,246,0.4)]" />
                                        )}
                                        
                                        {/* 修改状态指示点 */}
                                        {tab.hasChanges && (
                                            <div className="absolute right-2 top-1/2 -translate-y-1/2 w-1.5 h-1.5 bg-amber-500 rounded-full shadow-[0_0_8px_rgba(245,158,11,0.6)] animate-pulse" title="有未保存的修改" />
                                        )}
                                        
                                        <div className="flex items-center gap-2 w-full pl-1">
                                            <span className={`truncate relative z-10 ${isActive ? 'font-bold' : ''}`}>
                                                {tab.shortLabel}
                                            </span>
                                        </div>
                                        
                                        {tab.guideId && (
                                            <span className={`
                                                text-[10px] font-mono pl-1 truncate transition-colors
                                                ${isActive ? 'text-violet-400/80 font-semibold' : 'text-slate-400/70 group-hover:text-slate-400'}
                                            `}>
                                                {tab.guideId}
                                            </span>
                                        )}

                                        {/* 只有选中时显示的背景光效 */}
                                        {isActive && (
                                            <div className="absolute inset-0 bg-gradient-to-tr from-violet-500/5 via-transparent to-indigo-500/5 rounded-xl pointer-events-none" />
                                        )}
                                    </button>
                                )
                            })}
                        </div>
                    </div>

                    {/* 右侧内容区域 */}
                    <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar bg-slate-50/10">
                        <div className="p-4">
                            {/* 第一个Tab：一致性汇总 */}
                            {activeTab === 0 && (
                                <div className="bg-white/70 backdrop-blur-xl rounded-2xl border border-white/80 shadow-sm overflow-hidden">
                                    {/* 表格头部 - 渐变背景 */}
                                    <div className="bg-gradient-to-r from-violet-500 to-indigo-500 px-5 py-3.5">
                                        <div className="flex items-center gap-2">
                                            <div className="w-1 h-5 bg-white/40 rounded-full flex-shrink-0" />
                                            <h4 className="text-sm font-semibold text-white">
                                                {t('benchmark.details.consistency')}
                                            </h4>
                                        </div>
                                    </div>

                                    {/* 内容区域 */}
                                    <div className="p-4">
                                        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
                                        {/* 左侧：总览与指南 */}
                                        <div className="lg:col-span-4 space-y-4">
                                            {/* 总览区域 */}
                                            <div className="bg-white/50 backdrop-blur-xl p-4 rounded-2xl border border-white/60 relative overflow-hidden">
                                                <div className="absolute top-0 right-0 w-24 h-24 bg-gradient-to-br from-violet-500/5 to-fuchsia-500/5 rounded-bl-full" />

                                                <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-4 flex items-center gap-1.5">
                                                    <span className="w-1 h-1 rounded-full bg-violet-500" />
                                                    {t('benchmark.visualization.overall')}
                                                </h3>

                                                <div className="flex items-center justify-between gap-4">
                                                    <div className="flex flex-col items-center">
                                                        <ConsistencyRing value={summary.overall_consistency} />
                                                    </div>

                                                    <div className="grid grid-cols-2 gap-2 flex-1">
                                                        <StatCard
                                                            label={t('benchmark.visualization.successfulGuides')}
                                                            value={`${summary.success_count}`}
                                                            subValue={`Total: ${summary.total_guides}`}
                                                        />
                                                        <StatCard
                                                            label={t('benchmark.visualization.totalMatches')}
                                                            value={`${(summary.total_guides * 92).toLocaleString()}`}
                                                            subValue="Items × Evaluators"
                                                        />
                                                    </div>
                                                </div>
                                            </div>

                                            {/* 指南一致性列表 */}
                                            <div className="bg-white/50 backdrop-blur-xl p-4 rounded-2xl border border-white/60 flex flex-col">
                                                <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-3 flex items-center justify-between">
                                                    <div className="flex items-center gap-1.5">
                                                        <span className="w-1 h-1 rounded-full bg-blue-500" />
                                                        {t('benchmark.visualization.rank')}
                                                    </div>
                                                    <span className="text-[10px] font-normal normal-case bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full">
                                                        {t('benchmark.visualization.totalCount', { count: summary.guide_consistencies.length })}
                                                    </span>
                                                </h3>
                                                <GuideBarChart guides={summary.guide_consistencies} />
                                            </div>
                                        </div>

                                        {/* 右侧：条目分布 */}
                                        <div className="lg:col-span-8">
                                            <div className="bg-white/50 backdrop-blur-xl p-4 rounded-2xl border border-white/60">
                                                <div className="flex items-center justify-between mb-4">
                                                    <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wide flex items-center gap-1.5">
                                                        <span className="w-1 h-1 rounded-full bg-emerald-500" />
                                                        {t('benchmark.visualization.itemDistribution')}
                                                    </h3>

                                                    {/* 图例 */}
                                                    <div className="flex items-center gap-3 bg-slate-50/50 px-2 py-1 rounded-md border border-slate-100">
                                                        <div className="flex items-center gap-1 px-1.5">
                                                            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                                                            <span className="text-[10px] font-medium text-slate-600">{t('benchmark.visualization.legend.high')}</span>
                                                        </div>
                                                        <div className="flex items-center gap-1 px-1.5 border-l border-slate-200">
                                                            <div className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                                                            <span className="text-[10px] font-medium text-slate-600">{t('benchmark.visualization.legend.med')}</span>
                                                        </div>
                                                        <div className="flex items-center gap-1 px-1.5 border-l border-slate-200">
                                                            <div className="w-1.5 h-1.5 rounded-full bg-rose-500" />
                                                            <span className="text-[10px] font-medium text-slate-600">{t('benchmark.visualization.legend.low')}</span>
                                                        </div>
                                                    </div>
                                                </div>

                                                <DomainItemHeatmap items={summary.item_consistencies} />
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* 指南详情 Tab */}
                        {activeTab > 0 && details[activeTab - 1] && (
                            <div className="bg-white/70 backdrop-blur-xl rounded-2xl border border-white/80 shadow-sm overflow-hidden">
                                {/* 表格头部 - 渐变背景 */}
                                <div className="bg-gradient-to-r from-violet-500 to-indigo-500 px-5 py-3.5">
                                    <div className="flex items-center justify-between gap-3">
                                        <div className="flex items-center gap-2 min-w-0">
                                            <div className="w-1 h-5 bg-white/40 rounded-full flex-shrink-0" />
                                            <h4 className="text-sm font-semibold text-white truncate">
                                                {details[activeTab - 1].guide_name || details[activeTab - 1].guide_id}
                                            </h4>
                                        </div>
                                        {/* 指南编号徽章 */}
                                        {details[activeTab - 1].guide_id && (
                                            <span className="px-2.5 py-1 bg-white/20 backdrop-blur-sm rounded-lg text-xs font-mono text-white/95 border border-white/20 flex-shrink-0">
                                                {details[activeTab - 1].guide_id}
                                            </span>
                                        )}
                                    </div>
                                </div>

                                {/* 优化的表格 */}
                                <div className="p-3">
                                    <div className="rounded-xl border border-slate-200/80 overflow-hidden bg-white/50">
                                        <table className="w-full text-sm">
                                            <colgroup>
                                                <col style={{ width: '22%' }} />
                                                <col style={{ width: '19.5%' }} />
                                                <col style={{ width: '19.5%' }} />
                                                <col style={{ width: '19.5%' }} />
                                                <col style={{ width: '19.5%' }} />
                                            </colgroup>
                                            <thead>
                                                <tr className="bg-slate-50/90 border-b border-slate-200">
                                                    <th className="py-2 px-3 text-left font-semibold text-slate-700 text-xs">
                                                        {t('benchmark.details.item')}
                                                    </th>
                                                    <th className="py-2 px-2 text-center font-semibold text-slate-700 text-xs">
                                                        <div className="flex flex-col items-center gap-0.5">
                                                            <span className="text-[9px] uppercase tracking-wider text-blue-500 font-bold">
                                                                {t('benchmark.details.human')}
                                                            </span>
                                                            <span className="text-[11px] text-slate-600">
                                                                {summary.rater_names?.[0] || t('benchmark.details.rater1')}
                                                            </span>
                                                        </div>
                                                    </th>
                                                    <th className="py-2 px-2 text-center font-semibold text-slate-700 text-xs">
                                                        <div className="flex flex-col items-center gap-0.5">
                                                            <span className="text-[9px] uppercase tracking-wider text-blue-500 font-bold">
                                                                {t('benchmark.details.human')}
                                                            </span>
                                                            <span className="text-[11px] text-slate-600">
                                                                {summary.rater_names?.[1] || t('benchmark.details.rater2')}
                                                            </span>
                                                        </div>
                                                    </th>
                                                    <th className="py-2 px-2 text-center font-semibold text-slate-700 text-xs">
                                                        <div className="flex flex-col items-center gap-0.5">
                                                            <span className="text-[9px] uppercase tracking-wider text-violet-500 font-bold">
                                                                {t('benchmark.details.model')}
                                                            </span>
                                                            <span className="text-[11px] text-slate-600 whitespace-normal leading-tight">
                                                                {batchIds[0]?.replace('batch_', '') || t('benchmark.details.batch1')}
                                                            </span>
                                                        </div>
                                                    </th>
                                                    <th className="py-2 px-2 text-center font-semibold text-slate-700 text-xs">
                                                        <div className="flex flex-col items-center gap-0.5">
                                                            <span className="text-[9px] uppercase tracking-wider text-violet-500 font-bold">
                                                                {t('benchmark.details.model')}
                                                            </span>
                                                            <span className="text-[11px] text-slate-600 whitespace-normal leading-tight">
                                                                {batchIds[1]?.replace('batch_', '') || t('benchmark.details.batch2')}
                                                            </span>
                                                        </div>
                                                    </th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {details[activeTab - 1].items.map((item, idx) => {
                                                    const guideId = details[activeTab - 1].guide_id
                                                    const currentHuman1 = getCurrentScore(guideId, item.item_id, 'human_score1', item.human_score1)
                                                    const currentHuman2 = getCurrentScore(guideId, item.item_id, 'human_score2', item.human_score2)
                                                    const currentBatch1 = getCurrentScore(guideId, item.item_id, 'batch1_model_score', item.batch1_model_score)
                                                    const currentBatch2 = getCurrentScore(guideId, item.item_id, 'batch2_model_score', item.batch2_model_score)

                                                    // 计算评分差异
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
                                                                    ? 'bg-rose-50/80 hover:bg-rose-100/60 border-rose-200/50'
                                                                    : idx % 2 === 0
                                                                        ? 'bg-white hover:bg-slate-50/50 border-slate-100'
                                                                        : 'bg-slate-50/30 hover:bg-slate-50/60 border-slate-100'
                                                                }
                                                            `}
                                                        >
                                                            <td className="py-2 px-3">
                                                                <div className="flex items-center gap-1.5">
                                                                    <span className={`w-5 h-5 flex items-center justify-center rounded text-[9px] font-bold ${
                                                                        hasLargeDiff
                                                                            ? 'bg-rose-200 text-rose-700'
                                                                            : 'bg-slate-100 text-slate-500'
                                                                    }`}>
                                                                        {idx + 1}
                                                                    </span>
                                                                    <span className={`text-xs ${hasLargeDiff ? 'text-rose-900 font-medium' : 'text-slate-700'}`}>
                                                                        {item.item_name}
                                                                    </span>
                                                                </div>
                                                            </td>
                                                            <td className="py-2 px-2 text-center">
                                                                <EditableScore
                                                                    value={currentHuman1}
                                                                    originalValue={item.human_score1}
                                                                    onChange={(val) => handleScoreChange(guideId, item.item_id, 'human_score1', val)}
                                                                />
                                                            </td>
                                                            <td className="py-2 px-2 text-center">
                                                                <EditableScore
                                                                    value={currentHuman2}
                                                                    originalValue={item.human_score2}
                                                                    onChange={(val) => handleScoreChange(guideId, item.item_id, 'human_score2', val)}
                                                                />
                                                            </td>
                                                            <td className="py-2 px-2 text-center">
                                                                <EditableScore
                                                                    value={currentBatch1}
                                                                    originalValue={item.batch1_model_score}
                                                                    onChange={(val) => handleScoreChange(guideId, item.item_id, 'batch1_model_score', val)}
                                                                    isModel
                                                                />
                                                            </td>
                                                            <td className="py-2 px-2 text-center">
                                                                <EditableScore
                                                                    value={currentBatch2}
                                                                    originalValue={item.batch2_model_score}
                                                                    onChange={(val) => handleScoreChange(guideId, item.item_id, 'batch2_model_score', val)}
                                                                    isModel
                                                                />
                                                            </td>
                                                        </tr>
                                                    )
                                                })}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                    </div>
                </div>
            )}
        </div>
    )
}
