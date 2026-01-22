import { BenchmarkSummary, GuideConsistency, ItemConsistency } from '@/services/api'
import { useTranslation } from 'react-i18next'

interface Props {
    summary: BenchmarkSummary
}

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

// 总览统计卡片
function StatCard({ label, value, subValue }: { label: string; value: string; subValue?: string }) {
    return (
        <div className="relative overflow-hidden bg-white/50 rounded-2xl p-4 border border-white/60 shadow-sm transition-all hover:shadow-md hover:bg-white/80 group">
            <div className="relative z-10 flex flex-col items-center justify-center h-full">
                <p className="text-3xl font-bold bg-gradient-to-r from-violet-600 to-indigo-600 bg-clip-text text-transparent group-hover:scale-110 transition-transform duration-300">
                    {value}
                </p>
                <p className="text-sm font-medium text-slate-500 mt-1">{label}</p>
                {subValue && <p className="text-xs text-slate-400 mt-1">{subValue}</p>}
            </div>
            {/* 装饰背景 */}
            <div className="absolute -top-4 -right-4 w-16 h-16 bg-gradient-to-br from-violet-500/10 to-purple-500/10 rounded-full blur-xl group-hover:bg-violet-500/20 transition-colors" />
        </div>
    )
}

// 环形进度指示器
function ConsistencyRing({ value }: { value: number }) {
    const { t } = useTranslation()
    const percentage = Math.round(value * 100)
    const circumference = 2 * Math.PI * 45
    const strokeDashoffset = circumference - (value * circumference)

    return (
        <div className="relative w-40 h-40 flex items-center justify-center group">
            {/* 外部发光 */}
            <div className="absolute inset-0 bg-violet-500/10 rounded-full blur-2xl group-hover:bg-violet-500/20 transition-colors duration-500" />

            <svg className="w-full h-full transform -rotate-90 relative z-10" viewBox="0 0 100 100">
                {/* 背景圆环 */}
                <circle
                    cx="50"
                    cy="50"
                    r="45"
                    stroke="currentColor"
                    strokeWidth="8"
                    fill="none"
                    className="text-slate-100"
                />
                {/* 进度圆环 */}
                <circle
                    cx="50"
                    cy="50"
                    r="45"
                    stroke="url(#gradient-ring)"
                    strokeWidth="8"
                    fill="none"
                    strokeLinecap="round"
                    style={{
                        strokeDasharray: circumference,
                        strokeDashoffset: strokeDashoffset,
                        transition: 'stroke-dashoffset 1s cubic-bezier(0.4, 0, 0.2, 1)'
                    }}
                />
                <defs>
                    <linearGradient id="gradient-ring" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor="#8B5CF6" />
                        <stop offset="100%" stopColor="#6366F1" />
                    </linearGradient>
                </defs>
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center z-20">
                <span className="text-4xl font-black text-slate-800 tracking-tight">{percentage}%</span>
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider mt-1">{t('benchmark.visualization.consistency')}</span>
            </div>
        </div>
    )
}

// 指南一致性条形图
function GuideBarChart({ guides }: { guides: GuideConsistency[] }) {
    const { t } = useTranslation()
    // 按一致性排序
    const sorted = [...guides].sort((a, b) => b.consistency - a.consistency)

    if (sorted.length === 0) return <div className="text-center text-gray-400 py-8">{t('benchmark.visualization.noData')}</div>

    return (
        <div className="space-y-3 max-h-[600px] overflow-y-auto pr-2 custom-scrollbar">
            {sorted.map((guide, idx) => (
                <div key={guide.guide_id} className="group flex items-center gap-3 p-2 rounded-xl hover:bg-slate-50/80 transition-colors">
                    <span className="w-6 text-xs font-mono text-slate-400 text-right">{idx + 1}</span>
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between text-xs mb-1.5">
                            <span className="font-medium text-slate-700 truncate pr-2" title={guide.guide_name}>
                                {guide.guide_name || guide.guide_id}
                            </span>
                            <span className={`font-bold font-mono ${getConsistencyTextColor(guide.consistency)}`}>
                                {(guide.consistency * 100).toFixed(1)}%
                            </span>
                        </div>
                        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                            <div
                                className={`h-full bg-gradient-to-r ${getConsistencyColor(guide.consistency)} rounded-full shadow-sm transition-all duration-700 ease-out group-hover:brightness-110`}
                                style={{ width: `${guide.consistency * 100}%` }}
                            />
                        </div>
                    </div>
                </div>
            ))}
        </div>
    )
}

// 优化的条目一致性分布图（按领域分组）
function DomainItemHeatmap({ items }: { items: ItemConsistency[] }) {
    const { t } = useTranslation()

    // 创建条目映射以便快速查找
    const itemMap = new Map(items.map(i => {
        // 提取数字ID: "item_1" -> 1
        const id = parseInt(i.item_id.replace('item_', ''))
        return [id, i]
    }))

    return (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {DOMAINS.map((domain) => (
                <div key={domain.id} className="bg-white/40 border border-white/50 rounded-xl p-3 hover:bg-white/60 transition-colors">
                    <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2 flex items-center justify-between">
                        <span>Domain {domain.id}</span>
                        <span className="opacity-60 font-normal normal-case">
                            {t(`benchmark.visualization.domains.${domain.key}`)}
                        </span>
                    </h4>
                    <div className="flex flex-wrap gap-2">
                        {domain.items.map((itemId) => {
                            const item = itemMap.get(itemId)
                            if (!item) return null

                            const consistency = item.consistency
                            const isHigh = consistency >= 0.8

                            return (
                                <div
                                    key={itemId}
                                    className={`
                                        group relative flex flex-col items-center justify-center
                                        w-12 h-12 rounded-lg border border-transparent 
                                        transition-all duration-300 cursor-default
                                        ${getConsistencyLightBg(consistency)} 
                                        hover:scale-105 hover:shadow-md hover:border-current
                                    `}
                                    title={item.item_name}
                                >
                                    {/* 顶部：条目编号 */}
                                    <span className={`text-[10px] font-bold ${getConsistencyTextColor(consistency)} opacity-70`}>
                                        #{itemId}
                                    </span>

                                    {/* 中部：一致性百分比 */}
                                    <span className={`text-xs font-black tracking-tight ${getConsistencyTextColor(consistency)}`}>
                                        {(consistency * 100).toFixed(0)}%
                                    </span>

                                    {/* 底部指示条 */}
                                    <div className={`absolute bottom-0 left-2 right-2 h-0.5 rounded-full ${isHigh ? 'bg-emerald-500' : consistency >= 0.6 ? 'bg-amber-500' : 'bg-rose-500'} opacity-50`} />
                                </div>
                            )
                        })}
                    </div>
                </div>
            ))}
        </div>
    )
}

// 主可视化组件
export function BenchmarkVisualization({ summary }: Props) {
    const { t } = useTranslation()

    if (!summary) return null

    return (
        <div className="space-y-6 mt-8 animate-in fade-in slide-in-from-bottom-4 duration-700">
            {/* 玻璃效果样式定义 */}
            <style>{`
                .glass-panel {
                    background: rgba(255, 255, 255, 0.65);
                    backdrop-filter: blur(16px);
                    -webkit-backdrop-filter: blur(16px);
                    border: 1px solid rgba(255, 255, 255, 0.4);
                    box-shadow: 
                        0 4px 6px -1px rgba(0, 0, 0, 0.02), 
                        0 2px 4px -1px rgba(0, 0, 0, 0.02),
                        inset 0 0 0 1px rgba(255, 255, 255, 0.5);
                }
                .custom-scrollbar::-webkit-scrollbar {
                    width: 4px;
                }
                .custom-scrollbar::-webkit-scrollbar-track {
                    background: transparent;
                }
                .custom-scrollbar::-webkit-scrollbar-thumb {
                    background-color: rgba(156, 163, 175, 0.3);
                    border-radius: 20px;
                }
            `}</style>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
                {/* 左侧：总览与指南 (4/12) */}
                <div className="lg:col-span-4 space-y-6">
                    {/* 总览区域 */}
                    <div className="glass-panel p-6 rounded-3xl relative overflow-hidden">
                        <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-br from-violet-500/5 to-fuchsia-500/5 rounded-bl-full" />

                        <h3 className="text-sm font-bold text-slate-500 uppercase tracking-widest mb-6 flex items-center gap-2">
                            <span className="w-1.5 h-1.5 rounded-full bg-violet-500 shadow-lg shadow-violet-500/50" />
                            {t('benchmark.visualization.overall')}
                        </h3>

                        <div className="flex flex-col items-center mb-8">
                            <ConsistencyRing value={summary.overall_consistency} />
                        </div>

                        <div className="grid grid-cols-2 gap-3">
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

                    {/* 指南一致性列表 */}
                    <div className="glass-panel p-6 rounded-3xl flex flex-col min-h-[500px]">
                        <h3 className="text-sm font-bold text-slate-500 uppercase tracking-widest mb-4 flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <span className="w-1.5 h-1.5 rounded-full bg-blue-500 shadow-lg shadow-blue-500/50" />
                                {t('benchmark.visualization.rank')}
                            </div>
                            <span className="text-xs font-normal normal-case bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full">
                                {t('benchmark.visualization.totalCount', { count: summary.guide_consistencies.length })}
                            </span>
                        </h3>
                        <GuideBarChart guides={summary.guide_consistencies} />
                    </div>
                </div>

                {/* 右侧：条目分布 (8/12) */}
                <div className="lg:col-span-8">
                    <div className="glass-panel p-8 rounded-3xl h-full">
                        <div className="flex items-center justify-between mb-8">
                            <h3 className="text-sm font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-lg shadow-emerald-500/50" />
                                {t('benchmark.visualization.itemDistribution')}
                            </h3>

                            {/* 图例 */}
                            <div className="flex items-center gap-4 bg-slate-50/50 p-1.5 rounded-lg border border-slate-100">
                                <div className="flex items-center gap-1.5 px-2">
                                    <div className="w-2 h-2 rounded-full bg-emerald-500" />
                                    <span className="text-xs font-medium text-slate-600">{t('benchmark.visualization.legend.high')}</span>
                                </div>
                                <div className="flex items-center gap-1.5 px-2 border-l border-slate-200">
                                    <div className="w-2 h-2 rounded-full bg-amber-500" />
                                    <span className="text-xs font-medium text-slate-600">{t('benchmark.visualization.legend.med')}</span>
                                </div>
                                <div className="flex items-center gap-1.5 px-2 border-l border-slate-200">
                                    <div className="w-2 h-2 rounded-full bg-rose-500" />
                                    <span className="text-xs font-medium text-slate-600">{t('benchmark.visualization.legend.low')}</span>
                                </div>
                            </div>
                        </div>

                        <DomainItemHeatmap items={summary.item_consistencies} />

                        {/* 移除了底部的说明文字区域 */}
                    </div>
                </div>
            </div>
        </div>
    )
}
