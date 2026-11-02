import { useState, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { BarChart3, BookOpen, ChevronDown, ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { GuideDetail } from '@/services/api'

type ViewMode = 'consistency' | 'guide'

interface TabBarProps {
  viewMode: ViewMode
  selectedGuideIndex: number
  guideDetails: GuideDetail[]
  onViewModeChange: (mode: ViewMode) => void
  onGuideChange: (index: number) => void
  onPrevGuide: () => void
  onNextGuide: () => void
  hasPersistedChanges?: (guideId: string) => boolean
  guideIsSaving?: boolean
  guideHasUnsaved?: boolean
}

export function TabBar({
  viewMode,
  selectedGuideIndex,
  guideDetails,
  onViewModeChange,
  onGuideChange,
  onPrevGuide,
  onNextGuide,
  hasPersistedChanges,
  guideIsSaving,
  guideHasUnsaved
}: TabBarProps) {
  const { t } = useTranslation()
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const dropdownRef = useRef<HTMLDivElement>(null)

  // 过滤指南列表
  const filteredGuides = guideDetails.filter((guide) => {
    if (!searchQuery.trim()) return true
    const query = searchQuery.toLowerCase()
    return (
      guide.guide_name.toLowerCase().includes(query) ||
      guide.guide_id.toLowerCase().includes(query) ||
      `${t('benchmark.tabs.guide')} ${guide.seq_no}`.toLowerCase().includes(query)
    )
  })

  // 当前选中的指南
  const currentGuide = guideDetails[selectedGuideIndex]

  // 点击外部关闭下拉菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false)
        setSearchQuery('')
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // 键盘快捷键
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.altKey && e.key === 'ArrowLeft') {
      e.preventDefault()
      onPrevGuide()
    }
    if (e.altKey && e.key === 'ArrowRight') {
      e.preventDefault()
      onNextGuide()
    }
  }, [onPrevGuide, onNextGuide])

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  // 选择指南
  const handleSelectGuide = (index: number) => {
    onGuideChange(index)
    setIsDropdownOpen(false)
    setSearchQuery('')
  }

  return (
    <div className="bg-white border-b border-slate-200 sticky top-0 z-40">
      <div className="px-6 py-2">
        <div className="flex items-center gap-6">
          {/* 一致性分析 Tab */}
          <button
            onClick={() => onViewModeChange('consistency')}
            className={`
              relative px-4 py-2 rounded-lg transition-all duration-200 flex items-center gap-2.5 group
              ${viewMode === 'consistency'
                ? 'bg-blue-50 text-blue-700 font-medium'
                : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'
              }
            `}
          >
            <BarChart3 className={`w-4 h-4 ${viewMode === 'consistency' ? 'text-blue-600' : 'text-slate-400 group-hover:text-slate-500'}`} />
            <span className="text-sm">{t('benchmark.tabs.consistency')}</span>
            {viewMode === 'consistency' && (
              <div className="absolute bottom-0 left-0 w-full h-0.5 bg-blue-600 rounded-full" />
            )}
          </button>

          <div className="w-px h-6 bg-slate-200" />

          {/* 指南选择器区域 */}
          <div className="flex items-center gap-2 flex-1">
            {/* 当前指南显示 - 点击切换到指南模式 */}
            <button
              onClick={() => onViewModeChange('guide')}
              className={`
                relative px-4 py-2 rounded-lg transition-all duration-200 flex items-center gap-2.5 group
                ${viewMode === 'guide'
                  ? 'bg-blue-50 text-blue-700 font-medium'
                  : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'
                }
              `}
            >
              <BookOpen className={`w-4 h-4 ${viewMode === 'guide' ? 'text-blue-600' : 'text-slate-400 group-hover:text-slate-500'}`} />
              <span className="text-sm">
                {currentGuide ? (
                  <span className="flex items-center gap-2">
                    {currentGuide.guide_id}
                    {/* 自动保存状态提示 */}
                    {guideHasUnsaved && (
                      <span className={`
                        px-1.5 py-0.5 rounded text-xs font-medium flex items-center gap-1
                        ${guideIsSaving
                          ? 'bg-amber-50 text-amber-600'
                          : 'bg-amber-50 text-amber-700'
                        }
                      `}>
                        {guideIsSaving ? (
                          <>
                            <div className="w-3 h-3 border-2 border-amber-400/30 border-t-amber-400 rounded-full animate-spin" />
                            {t('benchmark.header.saving')}
                          </>
                        ) : (
                          <>
                            <span className="w-1.5 h-1.5 bg-amber-500 rounded-full flex-shrink-0" />
                            {t('benchmark.header.unsaved')}
                          </>
                        )}
                      </span>
                    )}
                    {hasPersistedChanges?.(currentGuide.guide_id) && !guideHasUnsaved && (
                      <span className="w-1.5 h-1.5 bg-amber-500 rounded-full flex-shrink-0 animate-pulse" title={t('benchmark.header.unsaved')} />
                    )}
                  </span>
                ) : (
                  <span className="text-slate-400">{t('benchmark.tabs.selectGuide')}</span>
                )}
              </span>
              {viewMode === 'guide' && (
                <div className="absolute bottom-0 left-0 w-full h-0.5 bg-blue-600 rounded-full" />
              )}
            </button>

            {/* 下拉菜单按钮 */}
            <div ref={dropdownRef} className="relative">
              <button
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                className={`
                  w-9 h-9 rounded-lg transition-all duration-200 flex items-center justify-center border
                  ${isDropdownOpen
                    ? 'bg-blue-50 border-blue-300 text-blue-600'
                    : 'bg-white border-slate-200 text-slate-400 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-600'
                  }
                `}
                title={t('benchmark.tabs.selectGuide')}
              >
                <ChevronDown className={`w-4 h-4 transition-transform duration-200 ${isDropdownOpen ? 'rotate-180' : ''}`} />
              </button>

              {/* 下拉菜单 */}
              {isDropdownOpen && (
                <div className="absolute top-full right-0 mt-2 w-80 bg-white rounded-xl border border-slate-200 shadow-xl shadow-slate-200/50 z-50 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
                  {/* 搜索框 */}
                  <div className="p-3 border-b border-slate-100 bg-slate-50/50">
                    <div className="relative">
                      <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                      <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder={t('benchmark.tabs.searchGuide')}
                        className="w-full pl-10 pr-4 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 bg-white transaction-all"
                        autoFocus
                      />
                    </div>
                  </div>

                  {/* 指南列表 */}
                  <div className="max-h-80 overflow-y-auto py-1">
                    {filteredGuides.length === 0 ? (
                      <div className="px-4 py-12 text-center">
                        <Search className="w-10 h-10 mx-auto mb-3 text-slate-300" />
                        <p className="text-sm text-slate-400">{t('benchmark.tabs.noGuideMatch')}</p>
                      </div>
                    ) : (
                      filteredGuides.map((guide) => {
                        const originalIndex = guideDetails.indexOf(guide)
                        const isSelected = originalIndex === selectedGuideIndex
                        const hasChanges = hasPersistedChanges?.(guide.guide_id)

                        return (
                          <button
                            key={guide.guide_id}
                            onClick={() => handleSelectGuide(originalIndex)}
                            className={`
                              w-full px-4 py-2.5 text-left flex items-start gap-3 transition-colors
                              ${isSelected
                                ? 'bg-blue-50'
                                : 'hover:bg-slate-50'
                              }
                            `}
                          >
                            <div className={`
                              mt-0.5 w-5 h-5 rounded flex items-center justify-center flex-shrink-0 text-xs font-bold border
                              ${isSelected
                                ? 'bg-blue-600 border-blue-600 text-white'
                                : 'bg-white border-slate-200 text-slate-500'
                              }
                            `}>
                              {guide.seq_no}
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className={`text-sm font-medium truncate ${isSelected ? 'text-blue-700' : 'text-slate-700'}`}>
                                {guide.guide_name}
                              </div>
                              <div className="text-xs text-slate-400 font-mono mt-0.5 truncate">
                                {guide.guide_id}
                              </div>
                            </div>
                            {hasChanges && (
                              <span className="w-2 h-2 rounded-full bg-amber-500 flex-shrink-0 mt-2" title={t('benchmark.header.unsaved')} />
                            )}
                          </button>
                        )
                      })
                    )}
                  </div>

                  {/* 底部计数 */}
                  {filteredGuides.length > 0 && (
                    <div className="px-4 py-2 border-t border-slate-100 bg-slate-50 flex items-center justify-between">
                      <span className="text-xs text-slate-500">
                        {t('benchmark.tabs.guideCount', { count: filteredGuides.length })}
                      </span>
                      {searchQuery && (
                        <button
                          onClick={() => setSearchQuery('')}
                          className="text-xs text-blue-600 hover:text-blue-700 font-medium"
                        >
                          {t('benchmark.tabs.clearFilter')}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* 导航按钮 */}
            {viewMode === 'guide' && guideDetails.length > 1 && (
              <>
                <div className="flex items-center gap-1 bg-white border border-slate-200 rounded-lg p-1">
                  <button
                    onClick={onPrevGuide}
                    disabled={selectedGuideIndex === 0}
                    className={`
                      p-1.5 rounded-md transition-all duration-200
                      ${selectedGuideIndex === 0
                        ? 'text-slate-300 cursor-not-allowed'
                        : 'text-slate-600 hover:text-blue-600 hover:bg-blue-50'
                      }
                    `}
                    title={t('benchmark.tabs.previousGuide')}
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <div className="w-px h-4 bg-slate-200" />
                  <button
                    onClick={onNextGuide}
                    disabled={selectedGuideIndex === guideDetails.length - 1}
                    className={`
                      p-1.5 rounded-md transition-all duration-200
                      ${selectedGuideIndex === guideDetails.length - 1
                        ? 'text-slate-300 cursor-not-allowed'
                        : 'text-slate-600 hover:text-blue-600 hover:bg-blue-50'
                      }
                    `}
                    title={t('benchmark.tabs.nextGuide')}
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </>
            )}

            {/* 指南计数 - 移动到最右侧，样式简化 */}
            {viewMode === 'guide' && (
              <div className="ml-auto text-xs font-medium text-slate-400 bg-slate-100 px-2 py-1 rounded">
                <span className="text-slate-600">{selectedGuideIndex + 1}</span> / {guideDetails.length}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
