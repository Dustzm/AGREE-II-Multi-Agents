/**
 * 引用（citations）辅助：把逐句定位的原句转成原文抽屉的高亮框。
 *
 * `evidence_sources` 是喂给模型阅读的全部片段，不是引用；有 `citations` 时按原句定位，
 * 旧结果没有 `citations`，调用方沿用按 chunk_id 定位。
 */
import type { Citation, EvidenceMatch } from '@/services/api'

/** 一条引用的全部区域（阅读顺序，第一个即跳转位置）；未定位时为空 */
export function citationHighlights(citation: Citation): EvidenceMatch[] {
    return citation.regions.map(region => ({
        page_idx: region.page_idx,
        bbox: region.bbox,
        text_preview: citation.text,
        chunk_id: region.chunk_id,
    }))
}

/** 引用首个区域所在页码（从 1 开始）；未定位时为 null */
export function citationPage(citation: Citation): number | null {
    const first = citation.regions[0]
    return first ? first.page_idx + 1 : null
}

/** 落在理由所依据 chunk 内的引用（任一区域命中即算），顺序与原句一致 */
export function reasonCitations(citations: Citation[], chunkIds: string[]): Citation[] {
    const ids = new Set(chunkIds)
    return citations.filter(citation => citation.regions.some(region => region.chunk_id !== null && ids.has(region.chunk_id)))
}
