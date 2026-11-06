import { describe, expect, it } from 'vitest'
import type { Citation } from '@/services/api'
import { citationHighlights, citationPage, reasonCitations } from '@/utils/citations'

const acrossPages: Citation = {
    text: 'Recommendations were reviewed by external experts before publication.',
    match: 'span',
    regions: [
        { page_idx: 4, bbox: [0.1, 0.85, 0.9, 0.95], block_id: 'b-40', chunk_id: 'c-7' },
        { page_idx: 5, bbox: [0.1, 0.05, 0.9, 0.12], block_id: 'b-41', chunk_id: 'c-8' },
    ],
}
const unplaced: Citation = { text: 'A paraphrase the locator could not place.', match: 'none', regions: [] }
const unchunked: Citation = {
    text: 'A block outside the evidence pool.',
    match: 'exact',
    regions: [{ page_idx: 0, bbox: [0, 0, 1, 0.1], block_id: 'b-1', chunk_id: null }],
}

describe('citation helpers', () => {
    it('turns every region of a citation into a highlight box in reading order', () => {
        expect(citationHighlights(acrossPages)).toEqual([
            { page_idx: 4, bbox: [0.1, 0.85, 0.9, 0.95], text_preview: acrossPages.text, chunk_id: 'c-7' },
            { page_idx: 5, bbox: [0.1, 0.05, 0.9, 0.12], text_preview: acrossPages.text, chunk_id: 'c-8' },
        ])
        expect(citationHighlights(unplaced)).toEqual([])
    })

    it('reports the one-based page of the first region, or null when unplaced', () => {
        expect(citationPage(acrossPages)).toBe(5)
        expect(citationPage(unchunked)).toBe(1)
        expect(citationPage(unplaced)).toBeNull()
    })

    it('links a reason to the citations inside its chunks, keeping quote order', () => {
        const citations = [unchunked, acrossPages, unplaced]
        expect(reasonCitations(citations, ['c-8'])).toEqual([acrossPages])
        expect(reasonCitations(citations, ['c-1'])).toEqual([])
        expect(reasonCitations(citations, [])).toEqual([])
    })
})
