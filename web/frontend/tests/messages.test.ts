import { afterEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import en from '../src/locales/en-US.json'
import zh from '../src/locales/zh-CN.json'
import { asMessage, legacyMessage, LocalizedError, renderMessage, responseMessage, uploadMessage } from '../src/i18n/messages'
import { taskEvent } from '../src/i18n/taskMessages'
import { formatDate } from '../src/i18n/format'
import i18n from '../src/i18n'
import { apiFetch, createAuthenticatedWebSocket, languageFetch, readJson, subscribeUnauthorized, UnauthorizedError } from '../src/services/http'
import { authService } from '../src/services/auth'

vi.mock('../src/config', () => ({ API_URL: '/ama/api', WS_URL: 'ws://test/ama/api' }))

async function translator(language: string) {
    const instance = createInstance()
    await instance.init({ lng: language, fallbackLng: 'en-US', resources: {
        'en-US': { translation: en, server: en.server }, 'zh-CN': { translation: zh, server: zh.server },
    } })
    return instance
}

afterEach(() => vi.unstubAllGlobals())

describe('message descriptors and historical compatibility', () => {
    it('retranslates a persisted message without changing its data', async () => {
        const message = responseMessage({ detail: 'Incorrect username or password.', i18n: { key: 'error.invalid_credentials', params: {} } })
        const saved = JSON.stringify(message)
        const instance = await translator('en-US')
        expect(renderMessage(message, instance.t)).toBe('Incorrect username or password.')
        await instance.changeLanguage('zh-CN')
        expect(renderMessage(JSON.parse(saved), instance.t)).toBe('用户名或密码错误。')
        expect(JSON.stringify(message)).toBe(saved)
    })

    it.each(['✓ File parsed (12 pages)', '✓ 文件解析完成 (12 页)'])('adapts old parsing completion: %s', text => {
        const event = taskEvent({ type: 'status', content: text })
        expect(event?.type).toBe('parsing')
        expect(event?.status).toBe('done')
        expect(event?.message.i18n?.params.pages).toBe('12')
    })

    it('uses explicit state without matching current text', () => {
        const event = taskEvent({ type: 'status', phase: 'parsing', state: 'done', content: 'arbitrary text', i18n: { key: 'status.parse_done_mineru', params: { pages: 12 } } })
        expect(event?.status).toBe('done')
        expect(event?.message.i18n?.params.pages).toBe(12)
    })

    it('adapts legacy batch steps and keeps unknown errors in diagnostics', async () => {
        expect(legacyMessage('i18n:batch.retrying:2:3')?.i18n).toEqual({ key: 'batch.retrying', params: { current: '2', max: '3' } })
        expect(legacyMessage('条目评价中')?.i18n?.key).toBe('batch.appraisingItems')
        const instance = await translator('zh-CN')
        const unknown = asMessage('Provider 500: original exception')
        expect(renderMessage(unknown, instance.t)).not.toContain('Provider 500')
        expect(unknown.diagnostic).toContain('Provider 500')
        expect(asMessage(new LocalizedError({ key: 'ui.pdfError' })).key).toBe('ui.pdfError')
        expect(JSON.stringify(asMessage('i18n:batch.failed:token="private"'))).not.toContain('private')
    })

    it('preserves validation arrays without printing objects or sensitive input', async () => {
        const message = responseMessage({ detail: [{ loc: ['body', 'password'], msg: 'too short', input: 'secret', i18n: { key: 'validation.short', params: { limit: 8 } } }] })
        expect(message.details?.[0].loc).toEqual(['body', 'password'])
        expect(JSON.stringify(message)).not.toContain('secret')
        const instance = await translator('zh-CN')
        expect(renderMessage(message, instance.t)).toBe('请检查请求中的字段。')
    })

    it('translates standard item names independently of workflow language', async () => {
        const instance = await translator('zh-CN')
        const text = renderMessage({ key: 'timeline.evaluating', params: { skill_id: 'item_1' } }, instance.t)
        expect(text).toContain(zh.standardItems.item_1)
    })

    it('uses a friendly fallback for unknown keys and redacts historical diagnostics', async () => {
        const instance = await translator('zh-CN')
        const message = responseMessage({ i18n: { key: 'future.error', params: {} }, detail: 'password="two words"' })
        expect(renderMessage(message, instance.t)).toBe(zh.server.error.unexpected)
        expect(message.diagnostic).toBe('password="[redacted]"')
        expect(asMessage('Bearer abc123').diagnostic).toBe('Bearer [redacted]')
        const validation = responseMessage({ detail: [{ loc: ['body', 'value'], msg: 'future reason token="private"', i18n: { key: 'future.validation', params: {} } }] })
        expect(validation.diagnostic).toBe('future reason token="[redacted]"')
    })

    it('retains per-file reasons for a partially successful upload', () => {
        const result = uploadMessage('chat.upload.result', { success_count: 1, failed_count: 1, failed_files: [{ filename: 'bad.txt', error: 'Only PDF files are supported.', i18n: { key: 'error.pdf_only', params: {} }, diagnostic: 'token="private"' }] })
        expect(result.params).toEqual({ success: 1, failed: 1 })
        expect(result.details?.[0].i18n?.key).toBe('error.pdf_only')
        expect(result.diagnostic).not.toContain('private')
    })

    it('uses locale-aware date formatting and preserves uninterpretable user text', () => {
        expect(formatDate('2026-10-03', 'en-US', { dateStyle: 'medium' })).toContain('Oct')
        expect(formatDate('2026-10-03', 'zh-CN', { dateStyle: 'medium' })).toContain('2026年')
        expect(formatDate('Author supplied date', 'zh-CN')).toBe('Author supplied date')
    })
})

describe('shared request transport', () => {
    it('carries the current language on WebSocket connections', async () => {
        await i18n.changeLanguage('zh-CN')
        const urls: string[] = []
        vi.stubGlobal('WebSocket', class {
            constructor(url: string) { urls.push(url) }
        })
        createAuthenticatedWebSocket('/batch/ws/test')
        expect(new URL(urls[0]).searchParams.get('lang')).toBe('zh-CN')
        expect(new URL(urls[0]).pathname).toBe('/ama/api/batch/ws/test')
    })

    it('sets the current language on all requests, including login', async () => {
        await i18n.changeLanguage('zh-CN')
        const fetch = vi.fn(async () => new Response(JSON.stringify({ authenticated: false, user: null }), { status: 200 }))
        vi.stubGlobal('fetch', fetch)
        await languageFetch('/test')
        await authService.login({ username: 'test', password: 'wrong' })
        for (const call of fetch.mock.calls as unknown as [string, RequestInit][]) {
            expect(new Headers(call[1].headers).get('Accept-Language')).toBe('zh-CN')
            expect(call[1].credentials).toBe('include')
        }
    })

    it('keeps login failure distinct from expiration of protected API sessions', async () => {
        const listener = vi.fn()
        const unsubscribe = subscribeUnauthorized(listener)
        vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'wrong', i18n: { key: 'error.invalid_credentials', params: {} } }), { status: 401 })))
        await expect(authService.login({ username: 'test', password: 'wrong' })).rejects.toBeInstanceOf(LocalizedError)
        expect(listener).not.toHaveBeenCalled()
        await expect(apiFetch('/protected')).rejects.toBeInstanceOf(UnauthorizedError)
        expect(listener).toHaveBeenCalledOnce()
        unsubscribe()
    })

    it('provides localized network and invalid-response fallbacks', async () => {
        vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline') }))
        await expect(apiFetch('/test')).rejects.toMatchObject({ data: { i18n: { key: 'error.network' }, diagnostic: 'offline' } })
        await expect(readJson(new Response('<html>Bad gateway</html>', { status: 502 }))).rejects.toMatchObject({ data: { i18n: { key: 'error.invalid_response' } } })
    })
})
