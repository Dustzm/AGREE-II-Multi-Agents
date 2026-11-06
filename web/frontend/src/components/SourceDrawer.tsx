import { MessageView } from '@/components/MessageView'
import { asMessage, type UiMessage } from '@/i18n/messages'
/**
 * SourceDrawer - 右侧抽屉式原文查看面板
 * 
 * 点击理由卡片后，在右侧滑出展示 PDF 原文并高亮匹配文本；
 * 传入 highlights（页码 + 归一化坐标）时直接高亮这些区域，不再按 chunk_id 查询位置
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Document, Page, pdfjs, type PageProps } from 'react-pdf'
import { X, ChevronLeft, ChevronRight, ZoomIn, ZoomOut, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api, type EvidenceMatch } from '@/services/api'
import { cn } from '@/utils/cn'
import { PDF_DOCUMENT_OPTIONS } from '@/utils/pdf'

// 配置 PDF.js worker
pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`

// 导入 react-pdf 样式
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'

interface SourceDrawerProps {
    isOpen: boolean
    onClose: () => void
    taskId: string
    folder?: string | null
    chunkIds?: string[]  // 优先使用 chunk_id 精确定位
    evidenceTexts?: string[]  // 备用文本匹配
    highlights?: EvidenceMatch[]  // 显式高亮框（如引用原句的定位），优先于 chunkIds，跳转到第一个
    reasonPoint?: string  // 当前理由要点
    reasonExplanation?: string // 理由详细解释
}

export function SourceDrawer({
    isOpen,
    onClose,
    taskId,
    folder,
    chunkIds,
    evidenceTexts,
    highlights,
    reasonPoint,
    reasonExplanation
}: SourceDrawerProps) {
    const { t } = useTranslation()
    const [matches, setMatches] = useState<EvidenceMatch[]>([])
    const [currentMatchIndex, setCurrentMatchIndex] = useState(0)
    const [loading, setLoading] = useState(false)
    const [pdfError, setPdfError] = useState<UiMessage | null>(null)
    const [locationError, setLocationError] = useState<UiMessage | null>(null)
    const [numPages, setNumPages] = useState(0)
    const [scale, setScale] = useState(0.8)
    const [pageSize, setPageSize] = useState<{ width: number; height: number } | null>(null)

    const pageContainerRef = useRef<HTMLDivElement>(null)

    // 显式高亮框：换一组时回到第一个位置（渲染期调整状态，避免 effect 内同步 setState）
    const explicit = Boolean(highlights && highlights.length > 0)
    const [shownHighlights, setShownHighlights] = useState(highlights)
    if (highlights !== shownHighlights) {
        setShownHighlights(highlights)
        setCurrentMatchIndex(0)
    }
    const shownMatches = explicit && highlights ? highlights : matches
    const busy = !explicit && loading

    // 加载证据位置
    useEffect(() => {
        // 优先使用 chunkIds，否则使用 evidenceTexts；有显式高亮框时不查询
        const hasData = (chunkIds && chunkIds.length > 0) || (evidenceTexts && evidenceTexts.length > 0)
        if (!isOpen || explicit || !hasData) return

        async function loadLocations() {
            setLoading(true)
            setLocationError(null)
            try {
                const result = await api.getEvidenceLocation(
                    taskId,
                    {
                        chunkIds: chunkIds?.length ? chunkIds : undefined,
                        evidenceTexts: !chunkIds?.length ? evidenceTexts : undefined
                    },
                    folder || undefined
                )
                setMatches(result.matches)
                setCurrentMatchIndex(0)
            } catch (error) {
                console.error('加载证据位置失败:', error)
                setLocationError(asMessage(error))
                setMatches([])
            } finally {
                setLoading(false)
            }
        }
        loadLocations()
    }, [isOpen, taskId, folder, chunkIds, evidenceTexts, explicit])

    // PDF 加载完成
    const onDocumentLoadSuccess = useCallback(({ numPages }: { numPages: number }) => {
        setPdfError(null)
        setNumPages(numPages)
    }, [])

    // 页面渲染完成
    const onPageRenderSuccess = useCallback(() => {
        if (pageContainerRef.current) {
            const canvas = pageContainerRef.current.querySelector('canvas')
            if (canvas) {
                setPageSize({
                    width: canvas.offsetWidth,
                    height: canvas.offsetHeight
                })
            }
        }
    }, [])

    const isUserScaled = useRef(false)

    // 切换匹配位置
    const goToPrevMatch = () => setCurrentMatchIndex(i => Math.max(0, i - 1))
    const goToNextMatch = () => setCurrentMatchIndex(i => Math.min(shownMatches.length - 1, i + 1))

    // 缩放
    const zoomIn = () => {
        isUserScaled.current = true
        setScale(s => Math.min(2.0, s + 0.1))
        setPageSize(null)
    }
    const zoomOut = () => {
        isUserScaled.current = true
        setScale(s => Math.max(0.5, s - 0.1))
        setPageSize(null)
    }

    // 页面加载成功：计算自适应宽度
    const onPageLoadSuccess = useCallback<NonNullable<PageProps['onLoadSuccess']>>((page) => {
        if (!isUserScaled.current && page.originalWidth) {
            const containerWidth = 480 - 32; // Drawer width (480px) - Padding (32px)
            // 减去 4px 缓冲区以防止舍入误差导致的滚动条
            const fitScale = (containerWidth - 4) / page.originalWidth
            setScale(fitScale)
        }
    }, [])

    const currentMatch = shownMatches[currentMatchIndex]

    // 页码变化时延迟重新检测 pageSize（等待 PDF 页面渲染完成）
    useEffect(() => {
        if (currentMatch) {
            const timer = setTimeout(() => {
                if (pageContainerRef.current) {
                    const canvas = pageContainerRef.current.querySelector('canvas')
                    if (canvas) {
                        setPageSize({
                            width: canvas.offsetWidth,
                            height: canvas.offsetHeight
                        })
                    }
                }
            }, 100)
            return () => clearTimeout(timer)
        }
    }, [currentMatchIndex, currentMatch, scale]) // scale 变化也需要更新 pageSize

    if (!isOpen) return null

    return (
        <>
            {/* 点击外部关闭遮罩 - 透明层 */}
            <div
                className="fixed inset-0 z-40"
                onClick={onClose}
            />

            <div
                className={cn(
                    "fixed top-0 right-0 h-full w-[480px] bg-white shadow-2xl border-l border-gray-200 z-50",
                    "transform transition-transform duration-300 ease-out flex flex-col", // 显式 flex-col
                    isOpen ? "translate-x-0" : "translate-x-full"
                )}
            >
                {/* 头部 */}
                <div className="flex items-center justify-between px-4 py-3 border-b bg-gray-50/80 shrink-0">
                    <div className="flex items-center gap-3 overflow-hidden">
                        <span className="text-xs font-bold text-gray-400 uppercase tracking-wider shrink-0">
                            {t('reports.source.title')}
                        </span>
                        {reasonPoint && (
                            <span className="text-sm font-bold text-gray-900 truncate" title={reasonPoint}>
                                {reasonPoint}
                            </span>
                        )}
                    </div>
                    <Button variant="ghost" size="sm" aria-label={t('ui.close')} onClick={onClose} className="h-7 w-7 p-0">
                        <X className="h-4 w-4" />
                    </Button>
                </div>

                {/* 理由解释展示区 - 紧跟标题 */}
                {reasonExplanation && (
                    <div className="bg-slate-50 border-b border-border/50 p-3 shrink-0 max-h-[160px] overflow-y-auto shadow-[inset_0_-2px_4px_rgba(0,0,0,0.02)]">
                        <div className="flex gap-2">
                            <div className="shrink-0 mt-0.5">
                                <div className="h-1.5 w-1.5 rounded-full bg-slate-400" />
                            </div>
                            <div>
                                <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-0.5">{t('ui.rationaleDetail')}</h4>
                                <p className="text-xs text-slate-700 leading-relaxed text-justify">
                                    {reasonExplanation}
                                </p>
                            </div>
                        </div>
                    </div>
                )}

                {/* 工具栏 - 移至 Detail 下方 */}
                {!explicit && locationError && <div className="p-4 text-red-600"><MessageView message={locationError} /></div>}
                {shownMatches.length > 0 && (
                    <div className="flex items-center justify-between px-4 py-2 border-b text-sm bg-white shrink-0 shadow-sm z-10">
                        {/* 匹配位置切换 */}
                        <div className="flex items-center gap-2">
                            <span className="text-gray-500">{t('reports.source.match')}</span>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={goToPrevMatch}
                                disabled={currentMatchIndex === 0}
                                className="h-6 w-6 p-0"
                            >
                                <ChevronLeft className="h-3 w-3" />
                            </Button>
                            <span className="font-mono text-xs min-w-[3rem] text-center">
                                {currentMatchIndex + 1} / {shownMatches.length}
                            </span>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={goToNextMatch}
                                disabled={currentMatchIndex >= shownMatches.length - 1}
                                className="h-6 w-6 p-0"
                            >
                                <ChevronRight className="h-3 w-3" />
                            </Button>
                        </div>

                        {/* 缩放 */}
                        <div className="flex items-center gap-1">
                            <Button variant="ghost" size="sm" onClick={zoomOut} className="h-6 w-6 p-0">
                                <ZoomOut className="h-3 w-3" />
                            </Button>
                            <span className="text-xs w-10 text-center">{Math.round(scale * 100)}%</span>
                            <Button variant="ghost" size="sm" onClick={zoomIn} className="h-6 w-6 p-0">
                                <ZoomIn className="h-3 w-3" />
                            </Button>
                        </div>
                    </div>
                )}

                {/* PDF 内容区 */}
                <div className="flex-1 overflow-auto p-4 bg-gray-100 min-h-0">
                    {busy ? (
                        <div className="flex items-center justify-center h-full">
                            <Loader2 className="h-6 w-6 animate-spin text-primary" />
                            <span className="ml-2 text-sm text-gray-500">
                                {t('reports.source.loading')}
                            </span>
                        </div>
                    ) : shownMatches.length === 0 ? (
                        <div className="flex items-center justify-center h-full text-gray-400">
                            <span className="text-sm">{t('reports.source.noMatch')}</span>
                        </div>
                    ) : (
                        <div ref={pageContainerRef} className="relative inline-block mx-auto">
                            <Document
                            error={<MessageView message={pdfError || { key: 'ui.pdfError' }} />}
                            onLoadError={error => setPdfError(asMessage(error, 'ui.pdfError'))}
                            onSourceError={error => setPdfError(asMessage(error, 'ui.pdfError'))}
                            noData={t('ui.noPdf')}
                                file={`/ama/api/reports/${taskId}/pdf`}
                                options={PDF_DOCUMENT_OPTIONS}
                                onLoadSuccess={onDocumentLoadSuccess}
                                loading={
                                    <div className="flex items-center justify-center h-64">
                                        <Loader2 className="h-5 w-5 animate-spin" />
                                    </div>
                                }
                            >
                                <Page
                                loading={t('ui.pageLoading')}
                                error={<MessageView message={pdfError || { key: 'ui.pageError' }} />}
                                onLoadError={error => setPdfError(asMessage(error, 'ui.pageError'))}
                                noData={t('ui.noPage')}
                                    pageNumber={(currentMatch?.page_idx ?? 0) + 1}
                                    scale={scale}
                                    renderTextLayer={false}
                                    renderAnnotationLayer={false}
                                    onRenderSuccess={onPageRenderSuccess}
                                    onLoadSuccess={onPageLoadSuccess}
                                />
                            </Document>

                            {/* 高亮叠加层 */}
                            {pageSize && shownMatches.length > 0 && (
                                <svg
                                    style={{
                                        position: 'absolute',
                                        top: 0,
                                        left: 0,
                                        width: pageSize.width,
                                        height: pageSize.height,
                                        pointerEvents: 'none'
                                    }}
                                >
                                    {/* 渲染当前页面的所有匹配区域 */}
                                    {shownMatches
                                        .filter(m => m.page_idx === currentMatch?.page_idx)
                                        .map((match, idx) => {
                                            const [x1, y1, x2, y2] = match.bbox
                                            // 判断是否是当前选中的匹配（通过原始匹配对象比较）
                                            const isActive = shownMatches[currentMatchIndex] === match

                                            // 计算略微扩展的边界，避免压住文字
                                            const pad = 2
                                            const rx = x1 * pageSize.width - pad
                                            const ry = y1 * pageSize.height - pad
                                            const rw = (x2 - x1) * pageSize.width + pad * 2
                                            const rh = (y2 - y1) * pageSize.height + pad * 2

                                            return (
                                                <g key={idx}>
                                                    {/* 背景填充 - 荧光笔效果 */}
                                                    <rect
                                                        x={x1 * pageSize.width}
                                                        y={y1 * pageSize.height}
                                                        width={(x2 - x1) * pageSize.width}
                                                        height={(y2 - y1) * pageSize.height}
                                                        fill={isActive ? '#facc15' : '#fde047'} // Yellow-400 / Yellow-300
                                                        fillOpacity={isActive ? 0.5 : 0.3}
                                                        style={{ mixBlendMode: 'multiply' }} // 让文字透过颜色变深
                                                    />
                                                    {/* 边框 - 稍微外扩 */}
                                                    <rect
                                                        x={rx}
                                                        y={ry}
                                                        width={rw}
                                                        height={rh}
                                                        fill="none"
                                                        stroke={isActive ? '#ea580c' : '#ca8a04'} // Orange-600 / Yellow-600
                                                        strokeWidth={isActive ? 2 : 1}
                                                        strokeDasharray={isActive ? 'none' : '3 2'}
                                                        rx="2" // 圆角
                                                    />
                                                    {/* 当前选中项显示标记 */}
                                                    {isActive && (
                                                        <>
                                                            <circle
                                                                cx={x1 * pageSize.width - 8}
                                                                cy={y1 * pageSize.height + (y2 - y1) * pageSize.height / 2}
                                                                r="6"
                                                                fill="#ea580c"
                                                            />
                                                            <circle
                                                                cx={x1 * pageSize.width - 8}
                                                                cy={y1 * pageSize.height + (y2 - y1) * pageSize.height / 2}
                                                                r="2.5"
                                                                fill="white"
                                                            />
                                                        </>
                                                    )}
                                                </g>
                                            )
                                        })}
                                </svg>
                            )}
                        </div>
                    )}
                </div>

                {/* 底部信息 */}
                {shownMatches.length > 0 && currentMatch && (
                    <div className="px-4 py-2 border-t bg-gray-50 text-xs text-gray-500">
                        <span>
                            {t('reports.source.page')} {(currentMatch.page_idx ?? 0) + 1} / {numPages}
                        </span>
                    </div>
                )}
            </div>
        </>
    )
}
