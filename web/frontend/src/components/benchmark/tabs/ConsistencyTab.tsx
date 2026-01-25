import { useTranslation } from 'react-i18next'
import { BenchmarkSummary } from '@/services/api'

// AGREE II 领域定义
const DOMAINS = [
  { id: 1, key: "scope", items: [1, 2, 3] },
  { id: 2, key: "stakeholder", items: [4, 5, 6] },
  { id: 3, key: "rigour", items: [7, 8, 9, 10, 11, 12, 13, 14] },
  { id: 4, key: "clarity", items: [15, 16, 17] },
  { id: 5, key: "applicability", items: [18, 19, 20, 21] },
  { id: 6, key: "independence", items: [22, 23] },
] as const

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

interface ConsistencyTabProps {
  summary: BenchmarkSummary
}

export function ConsistencyTab({ summary }: ConsistencyTabProps) {
  const { t } = useTranslation()

  const percentage = Math.round(summary.overall_consistency * 100)
  const circumference = 2 * Math.PI * 42
  const strokeDashoffset = circumference - (summary.overall_consistency * circumference)

  return (
    <div className="p-6">
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* 左侧：总览与指南 */}
        <div className="lg:col-span-4 space-y-6">
          {/* 总览区域 */}
          <div className="bg-white rounded-2xl border border-slate-200 p-6">
            <h3 className="text-sm font-bold text-slate-500 uppercase tracking-wider mb-6 flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-blue-600" />
              {t('benchmark.visualization.overall')}
            </h3>

            <div className="flex items-center justify-center gap-6">
              {/* 环形进度 */}
              <div className="relative w-36 h-36 flex items-center justify-center">
                <svg className="w-full h-full transform -rotate-90" viewBox="0 0 100 100">
                  <circle
                    cx="50" cy="50" r="42"
                    stroke="currentColor"
                    strokeWidth="8"
                    fill="none"
                    className="text-slate-100"
                  />
                  <circle
                    cx="50" cy="50" r="42"
                    stroke="url(#gradient)"
                    strokeWidth="8"
                    fill="none"
                    strokeLinecap="round"
                    style={{
                      strokeDasharray: circumference,
                      strokeDashoffset: strokeDashoffset,
                      transition: 'stroke-dashoffset 1s ease-out'
                    }}
                  />
                  <defs>
                    <linearGradient id="gradient" x1="0%" y1="0%" x2="100%" y2="0%">
                      <stop offset="0%" stopColor="#2563EB" />
                      <stop offset="100%" stopColor="#6366F1" />
                    </linearGradient>
                  </defs>
                </svg>
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <span className="text-3xl font-black text-slate-800">{percentage}%</span>
                  <span className="text-[10px] font-bold text-slate-500 uppercase mt-1">
                    {t('benchmark.visualization.consistency')}
                  </span>
                </div>
              </div>
            </div>

            {/* 统计卡片 */}
            <div className="grid grid-cols-2 gap-3 mt-6">
              <div className="bg-slate-50 rounded-xl p-3 text-center">
                <p className="text-xs text-slate-500">{t('benchmark.visualization.successfulGuides')}</p>
                <p className="text-xl font-bold text-slate-800">
                  {summary.success_count}/{summary.total_guides}
                </p>
              </div>
              <div className="bg-slate-50 rounded-xl p-3 text-center">
                <p className="text-xs text-slate-500">{t('benchmark.visualization.totalMatches')}</p>
                <p className="text-xl font-bold text-slate-800">
                  {(summary.total_guides * 92).toLocaleString()}
                </p>
              </div>
            </div>
          </div>

          {/* 指南一致性列表 */}
          <div className="bg-white rounded-2xl border border-slate-200 p-6">
            <h3 className="text-sm font-bold text-slate-500 uppercase tracking-wider mb-4 flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-blue-500" />
                {t('benchmark.visualization.rank')}
              </div>
            </h3>

            <div className="space-y-3 max-h-[400px] overflow-y-auto">
              {[...summary.guide_consistencies]
                .sort((a, b) => b.consistency - a.consistency)
                .map((guide, idx) => (
                  <div
                    key={guide.guide_id}
                    className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-slate-50 hover:bg-slate-100 transition-colors"
                  >
                    <div className={`
                        w-6 h-6 flex items-center justify-center rounded-lg text-[10px] font-bold flex-shrink-0
                        ${idx === 0 ? 'bg-yellow-100 text-yellow-700' :
                        idx === 1 ? 'bg-slate-100 text-slate-600' :
                          idx === 2 ? 'bg-orange-100 text-orange-700' :
                            'bg-transparent text-slate-400'}
                      `}>
                      {idx + 1}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs font-medium text-slate-700 truncate">
                          {t('benchmark.visualization.guide')} {guide.seq_no}
                        </span>
                        <span className={`text-xs font-bold ${getConsistencyTextColor(guide.consistency)}`}>
                          {(guide.consistency * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="h-1 bg-slate-200 rounded-full overflow-hidden">
                        <div
                          className={`h-full bg-gradient-to-r ${getConsistencyColor(guide.consistency)} rounded-full transition-all`}
                          style={{ width: `${guide.consistency * 100}%` }}
                        />
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        </div>

        {/* 右侧：条目分布 */}
        <div className="lg:col-span-8">
          <div className="bg-white rounded-2xl border border-slate-200 p-6">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-sm font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-emerald-500" />
                {t('benchmark.visualization.itemDistribution')}
              </h3>

              {/* 图例 */}
              <div className="flex items-center gap-3 bg-slate-50 px-3 py-1.5 rounded-lg">
                <div className="flex items-center gap-1.5">
                  <div className="w-2 h-2 rounded-full bg-emerald-500" />
                  <span className="text-xs text-slate-600">{t('benchmark.visualization.legend.high')}</span>
                </div>
                <div className="flex items-center gap-1.5 border-l border-slate-200 pl-3">
                  <div className="w-2 h-2 rounded-full bg-amber-500" />
                  <span className="text-xs text-slate-600">{t('benchmark.visualization.legend.med')}</span>
                </div>
                <div className="flex items-center gap-1.5 border-l border-slate-200 pl-3">
                  <div className="w-2 h-2 rounded-full bg-rose-500" />
                  <span className="text-xs text-slate-600">{t('benchmark.visualization.legend.low')}</span>
                </div>
              </div>
            </div>

            {/* 领域热力图 */}
            <div className="grid grid-cols-3 gap-4">
              {DOMAINS.map((domain) => {
                const itemMap = new Map(summary.item_consistencies.map(i => {
                  const id = parseInt(i.item_id.replace('item_', ''))
                  return [id, i]
                }))

                return (
                  <div key={domain.id} className="bg-slate-50 rounded-xl p-4">
                    <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-1.5 pb-2 border-b border-slate-200">
                      <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />
                      D{domain.id}
                      <span className="text-slate-400 font-normal">
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
                              w-11 h-11 rounded-lg border transition-all
                              ${getConsistencyLightBg(consistency)}
                              hover:scale-110 hover:shadow-md
                            `}
                            title={`${item.item_name}: ${(consistency * 100).toFixed(0)}%`}
                          >
                            <span className={`text-[10px] font-bold opacity-60 ${getConsistencyTextColor(consistency)}`}>
                              #{itemId}
                            </span>
                            <span className={`text-[11px] font-black ${getConsistencyTextColor(consistency)}`}>
                              {(consistency * 100).toFixed(0)}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
