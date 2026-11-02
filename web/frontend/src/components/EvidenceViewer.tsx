import { MessageView } from '@/components/MessageView'
import { asMessage, type UiMessage } from '@/i18n/messages'
/**
 * 证据池可视化组件
 * 
 * 以 PDF 页面为单位渲染，用不同颜色标注各语义类别区域
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Document, Page, pdfjs } from 'react-pdf'
import { Loader2, ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiFetch, readJson, buildApiUrl } from '@/services/http'
import { PDF_DOCUMENT_OPTIONS } from '@/utils/pdf'

// 配置 PDF.js worker
pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`

// 导入 react-pdf 样式
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'

// 类型定义
interface Region {
    id: string
    chunk_id: string | null
    label: string
    bbox: number[]  // [x1, y1, x2, y2] 归一化坐标 0-1
    type: string
    title: string
    reasoning: string
    text_preview: string
}

interface PageData {
    page_idx: number
    regions: Region[]
}

interface VisualEvidence {
    version: string
    page_count: number
    label_colors: Record<string, string>
    stats: Record<string, number>
    pages: PageData[]
}

interface EvidenceViewerProps {
    taskId: string
}

// 标签颜色配置（备用）
const DEFAULT_LABEL_COLORS: Record<string, string> = {
    Overview: '#3B82F6',           // 蓝色
    'Executive Summary': '#6366F1', // 靖蓝色
    Method: '#8B5CF6',             // 紫色
    Recommendation: '#10B981',     // 绿色
    Conclusion: '#14B8A6',         // 青色
    Others: '#F59E0B',             // 橙色
    Appendix: '#78716C',           // 灰棕色
    Irrelevant: '#9CA3AF'          // 灰色
}

export function EvidenceViewer({ taskId }: EvidenceViewerProps) {
    const { t } = useTranslation()
    const [visualEvidence, setVisualEvidence] = useState<VisualEvidence | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<UiMessage | null>(null)
    const [pdfError, setPdfError] = useState<UiMessage | null>(null)
    const [currentPage, setCurrentPage] = useState(0)
    const [numPages, setNumPages] = useState(0)
    const [scale, setScale] = useState(1.0)
    const [selectedRegion, setSelectedRegion] = useState<Region | null>(null)
    const [pageSize, setPageSize] = useState<{ width: number; height: number } | null>(null)

    const pageContainerRef = useRef<HTMLDivElement>(null)
    const pdfFile = useMemo(() => buildApiUrl(`/reports/${taskId}/pdf`), [taskId])

    // 加载可视化证据数据
    useEffect(() => {
        async function loadData() {
            try {
                setLoading(true)
                const response = await apiFetch(buildApiUrl(`/reports/${taskId}/visual_evidence`))
                const data = await readJson<VisualEvidence>(response)
                setVisualEvidence(data)
            } catch (e) {
                setError(asMessage(e))
            } finally {
                setLoading(false)
            }
        }
        loadData()
    }, [taskId, t])

    // PDF 加载完成回调
    const onDocumentLoadSuccess = useCallback(({ numPages }: { numPages: number }) => {
        setPdfError(null)
        setNumPages(numPages)
    }, [])

    const syncPageSize = useCallback(() => {
        if (pageContainerRef.current) {
            const canvas = pageContainerRef.current.querySelector('canvas')
            if (canvas) {
                const nextSize = {
                    width: canvas.offsetWidth,
                    height: canvas.offsetHeight
                }
                setPageSize((prev) => {
                    if (prev?.width === nextSize.width && prev?.height === nextSize.height) {
                        return prev
                    }
                    return nextSize
                })
            }
        }
    }, [])

    // 页面渲染完成回调 - 获取实际 canvas 尺寸
    const onPageRenderSuccess = useCallback(() => {
        syncPageSize()
    }, [syncPageSize])

    // 获取标签名称的国际化函数
    const getLabelName = (label: string) => {
        const labelKeyMap: Record<string, string> = {
            'Overview': 'evidence.label.overview',
            'Executive Summary': 'evidence.label.executiveSummary',
            'Method': 'evidence.label.method',
            'Recommendation': 'evidence.label.recommendation',
            'Conclusion': 'evidence.label.conclusion',
            'Others': 'evidence.label.others',
            'Appendix': 'evidence.label.appendix',
            'Irrelevant': 'evidence.label.irrelevant'
        }
        return labelKeyMap[label] ? t(labelKeyMap[label]) : label
    }

    // 获取当前页的标注区域
    const currentPageData = visualEvidence?.pages[currentPage]
    const labelColors = visualEvidence?.label_colors || DEFAULT_LABEL_COLORS

    // 页面导航
    const goToPrevPage = () => {
        setCurrentPage(p => Math.max(0, p - 1))
        setPageSize(null) // 重置页面尺寸
    }
    const goToNextPage = () => {
        setCurrentPage(p => Math.min(numPages - 1, p + 1))
        setPageSize(null) // 重置页面尺寸
    }

    // 缩放控制
    const zoomIn = () => {
        setScale(s => Math.min(2.0, s + 0.2))
        setPageSize(null)
    }
    const zoomOut = () => {
        setScale(s => Math.max(0.5, s - 0.2))
        setPageSize(null)
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center h-96">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
                <span className="ml-2">{t('evidence.loading')}</span>
            </div>
        )
    }

    if (error) {
        return (
            <div className="flex items-center justify-center h-96 text-red-500">
                <MessageView message={error} />
            </div>
        )
    }

    return (
        <div className="flex h-[calc(100vh-200px)] bg-gray-50 rounded-xl overflow-hidden border">
            {/* 左侧：PDF 视图 */}
            <div className="flex-1 flex flex-col overflow-hidden">
                {/* 工具栏 */}
                <div className="flex items-center gap-4 px-4 py-2 bg-white border-b overflow-x-auto">
                    {/* 页码导航 */}
                    <div className="flex items-center gap-2 flex-shrink-0">
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={goToPrevPage}
                            disabled={currentPage === 0}
                        >
                            <ChevronLeft className="h-4 w-4" />
                        </Button>
                        <span className="text-sm font-medium px-3 whitespace-nowrap min-w-[4rem] text-center">
                            {t('evidence.pagination', { current: currentPage + 1, total: numPages })}
                        </span>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={goToNextPage}
                            disabled={currentPage >= numPages - 1}
                        >
                            <ChevronRight className="h-4 w-4" />
                        </Button>
                    </div>

                    {/* 缩放控制 */}
                    <div className="flex items-center gap-2 flex-shrink-0">
                        <Button variant="outline" size="sm" onClick={zoomOut}>
                            <ZoomOut className="h-4 w-4" />
                        </Button>
                        <span className="text-sm w-12 text-center whitespace-nowrap">{Math.round(scale * 100)}%</span>
                        <Button variant="outline" size="sm" onClick={zoomIn}>
                            <ZoomIn className="h-4 w-4" />
                        </Button>
                    </div>

                    {/* 标签图例 */}
                    <div className="flex items-center gap-3 ml-auto min-w-0 overflow-x-auto scrollbar-hide">
                        {Object.entries(labelColors).map(([label, color]) => (
                            <div key={label} className="flex items-center gap-1 flex-shrink-0">
                                <div
                                    className="w-3 h-3 rounded"
                                    style={{ backgroundColor: color }}
                                />
                                <span className="text-xs text-gray-600 whitespace-nowrap">
                                    {getLabelName(label)}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>

                {/* PDF 内容区 */}
                <div className="flex-1 overflow-auto p-4 flex justify-center">
                    <div ref={pageContainerRef} className="relative inline-block">
                        <Document
                            error={<MessageView message={pdfError || { key: 'ui.pdfError' }} />}
                            onLoadError={error => setPdfError(asMessage(error, 'ui.pdfError'))}
                            onSourceError={error => setPdfError(asMessage(error, 'ui.pdfError'))}
                            noData={t('ui.noPdf')}
                            file={pdfFile}
                            options={PDF_DOCUMENT_OPTIONS}
                            onLoadSuccess={onDocumentLoadSuccess}
                            loading={
                                <div className="flex items-center justify-center h-96">
                                    <Loader2 className="h-6 w-6 animate-spin" />
                                </div>
                            }
                        >
                            <Page
                                loading={t('ui.pageLoading')}
                                error={<MessageView message={pdfError || { key: 'ui.pageError' }} />}
                                onLoadError={error => setPdfError(asMessage(error, 'ui.pageError'))}
                                noData={t('ui.noPage')}
                                pageNumber={currentPage + 1}
                                scale={scale}
                                renderTextLayer={false}
                                renderAnnotationLayer={false}
                                onRenderSuccess={onPageRenderSuccess}
                            />
                        </Document>

                        {/* SVG 标注叠加层 - 精确覆盖在 canvas 上 */}
                        {pageSize && currentPageData && (
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
                                {currentPageData.regions.map(region => {
                                    const [x1, y1, x2, y2] = region.bbox
                                    const color = labelColors[region.label] || '#9CA3AF'

                                    // 使用归一化坐标计算像素位置
                                    const pixelX = x1 * pageSize.width
                                    const pixelY = y1 * pageSize.height
                                    const pixelWidth = (x2 - x1) * pageSize.width
                                    const pixelHeight = (y2 - y1) * pageSize.height

                                    return (
                                        <rect
                                            key={region.id}
                                            x={pixelX}
                                            y={pixelY}
                                            width={pixelWidth}
                                            height={pixelHeight}
                                            fill={color}
                                            fillOpacity={selectedRegion?.id === region.id ? 0.4 : 0.2}
                                            stroke={color}
                                            strokeWidth={selectedRegion?.id === region.id ? 2 : 1}
                                            style={{ pointerEvents: 'auto', cursor: 'pointer' }}
                                            onClick={() => setSelectedRegion(region)}
                                        />
                                    )
                                })}
                            </svg>
                        )}
                    </div>
                </div>
            </div>

            {/* 右侧：详情面板 */}
            <div className="w-80 bg-white border-l flex flex-col overflow-auto p-4">
                {selectedRegion ? (
                    <div className="space-y-4">
                        {/* Chunk ID */}
                        {selectedRegion.chunk_id && (
                            <div>
                                <label className="text-xs text-gray-500 block mb-1">{t('ui.chunkId')}</label>
                                <code className="text-sm font-mono bg-gray-100 px-2 py-1 rounded">
                                    {selectedRegion.chunk_id}
                                </code>
                            </div>
                        )}

                        {/* 标签 */}
                        <div>
                            <label className="text-xs text-gray-500 block mb-1">{t('evidence.detail.semanticLabel')}</label>
                            <div className="flex items-center gap-2">
                                <div
                                    className="w-4 h-4 rounded"
                                    style={{ backgroundColor: labelColors[selectedRegion.label] }}
                                />
                                <span className="font-medium">
                                    {getLabelName(selectedRegion.label)}
                                </span>
                            </div>
                        </div>

                        {/* 标题 */}
                        {selectedRegion.title && (
                            <div>
                                <label className="text-xs text-gray-500 block mb-1">{t('evidence.detail.section')}</label>
                                <p className="text-sm">{selectedRegion.title}</p>
                            </div>
                        )}

                        {/* 内容类型 */}
                        <div>
                            <label className="text-xs text-gray-500 block mb-1">{t('evidence.detail.contentType')}</label>
                            <p className="text-sm">{selectedRegion.type}</p>
                        </div>

                        {/* 分类理由 */}
                        {selectedRegion.reasoning && (
                            <div>
                                <label className="text-xs text-gray-500 block mb-1">{t('evidence.detail.reason')}</label>
                                <p className="text-sm bg-gray-50 p-3 rounded-lg border">
                                    {selectedRegion.reasoning}
                                </p>
                            </div>
                        )}

                        {/* 内容预览 */}
                        {selectedRegion.text_preview && (
                            <div>
                                <label className="text-xs text-gray-500 block mb-1">{t('evidence.detail.preview')}</label>
                                <p className="text-sm text-gray-600 bg-gray-50 p-3 rounded-lg border">
                                    {selectedRegion.text_preview}...
                                </p>
                            </div>
                        )}
                    </div>
                ) : (
                    <div className="flex flex-col items-center justify-center h-full text-gray-400">
                        <p className="text-sm">{t('evidence.detail.clickHint1')}</p>
                        <p className="text-sm">{t('evidence.detail.clickHint2')}</p>
                    </div>
                )}
            </div>
        </div>
    )
}
