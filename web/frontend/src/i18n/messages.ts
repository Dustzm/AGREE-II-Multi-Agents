import type { TFunction } from 'i18next'
import en from '@/locales/en-US.json'
import zh from '@/locales/zh-CN.json'

export interface MessageDescriptor {
    key: string
    params: Record<string, unknown>
}

export interface ValidationMessage {
    loc: (string | number)[]
    msg: string
    i18n?: MessageDescriptor
}

export interface UiMessage {
    /** Client keys use the existing translation namespace. */
    key?: string
    params?: Record<string, unknown>
    i18n?: MessageDescriptor | null
    diagnostic?: string | null
    details?: ValidationMessage[]
}

export class LocalizedError extends Error {
    readonly data: UiMessage

    constructor(data: UiMessage, message = 'Request failed') {
        super(message)
        this.name = 'LocalizedError'
        this.data = data
    }
}

function flatten(value: Record<string, unknown>, prefix = '', output: Record<string, string> = {}) {
    for (const [key, child] of Object.entries(value)) {
        const name = prefix ? `${prefix}.${key}` : key
        if (typeof child === 'string') output[name] = child
        else if (child && typeof child === 'object') flatten(child as Record<string, unknown>, name, output)
    }
    return output
}

// Historical messages are adapted at the boundary; new events carry descriptors.
const legacyTemplates = [en, zh].flatMap(locale => {
    const resources = locale as unknown as Record<string, unknown>
    const server = flatten((resources.server || {}) as Record<string, unknown>)
    const client = flatten({ timeline: locale.timeline, chat: { status: locale.chat.status } })
    return [...Object.entries(server).map(([key, template]) => ({ key, template, server: true })), ...Object.entries(client).map(([key, template]) => ({ key, template, server: false }))]
        .filter(({ template }) => template.replace(/\{\{[^}]+\}\}/g, '').trim().length > 3)
        .map(entry => {
            const names: string[] = []
            const escaped = entry.template.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
            const pattern = escaped.replace(/\\\{\\\{([^}]+)\\\}\\\}/g, (_match, name: string) => {
                names.push(name)
                return '(.+?)'
            })
            return { ...entry, names, pattern: new RegExp(`^${pattern}$`) }
        })
})

const oldBatchSteps: Record<string, string> = {
    '正在初始化...': 'initializing', '初始化中': 'initializing', '解析 PDF 中': 'parsingPdf',
    '构建证据池中': 'buildingEvidence', '条目评价中': 'appraisingItems', '生成报告中': 'generatingReport',
    '重试中': 'retryingStep', '失败': 'failedGeneric', '等待继续': 'waitingContinue',
    '服务停止，等待继续': 'serviceStopWaiting', 'Worker 中断，等待继续': 'workerInterrupted',
    '已停止': 'stopped', '已完成': 'completed',
}

const serverKeys = new Set(Object.keys(flatten(en.server)))

export function sanitizeDiagnostic(text: string): string {
    return text.replace(/((?:api[_-]?key|password|secret|token|authorization)["']?\s*[:=]\s*)(["'])(.*?)\2/gi, '$1$2[redacted]$2')
        .replace(/((?:api[_-]?key|password|secret|token|authorization)["']?\s*[:=]\s*)[^\s,;&"'}]+/gi, '$1[redacted]')
        .replace(/(bearer\s+)[^\s,;"']+/gi, '$1[redacted]')
        .replace(/(https?:\/\/)[^/\s@]+@/g, '$1[redacted]@')
        .slice(0, 4000)
}

export function legacyMessage(text: string): UiMessage | null {
    if (oldBatchSteps[text]) return { i18n: { key: `batch.${oldBatchSteps[text]}`, params: {} } }
    if (text.startsWith('i18n:batch.')) {
        const [, key, ...args] = text.split(':')
        if (key === 'batch.failed') return { i18n: { key: 'batch.failedGeneric', params: {} }, diagnostic: args.join(':') }
        const names = key === 'batch.retrying' ? ['current', 'max'] : key === 'batch.itemProgress' ? ['completed', 'total'] : []
        return { i18n: { key, params: Object.fromEntries(names.map((name, i) => [name, args[i]])) } }
    }
    for (const entry of legacyTemplates) {
        const match = entry.pattern.exec(text)
        if (!match) continue
        const params = Object.fromEntries(entry.names.map((name, index) => [name, match[index + 1]]))
        return entry.server ? { i18n: { key: entry.key, params } } : { key: entry.key, params }
    }
    return null
}

export function asMessage(value: unknown, fallback = 'common.error.unknown'): UiMessage {
    if (value instanceof LocalizedError) return asMessage(value.data, fallback)
    if (value && typeof value === 'object' && !(value instanceof Error)) {
        const data = value as UiMessage & { content?: string }
        const unknown = data.i18n && !serverKeys.has(data.i18n.key)
        return { ...data, key: data.key || (unknown ? fallback : undefined), diagnostic: data.diagnostic ? sanitizeDiagnostic(data.diagnostic) : unknown ? sanitizeDiagnostic(data.content || JSON.stringify(data.i18n)) : undefined }
    }
    const text = value instanceof Error ? value.message : typeof value === 'string' ? value : ''
    const legacy = legacyMessage(text)
    return legacy ? { ...legacy, diagnostic: legacy.diagnostic ? sanitizeDiagnostic(legacy.diagnostic) : undefined }
        : { key: fallback, diagnostic: text ? sanitizeDiagnostic(text) : undefined }
}

export function renderMessage(message: UiMessage, t: TFunction, fallback = 'common.error.unknown', language = 'en'): string {
    const key = message.i18n ? `server:${message.i18n.key}` : message.key || fallback
    const params = { ...(message.i18n?.params || message.params) }
    if (typeof params.skill_id === 'string') {
        const lang = language.startsWith('zh') ? 'zh' : 'en'
        params.title = params[`title_${lang}`] || t(`standardItems.${params.skill_id}`, { defaultValue: params.skill_id })
    }
    return t(key, { ...params, defaultValue: t(message.key || fallback) }) as string
}

export function responseMessage(body: Record<string, unknown>, fallback = 'error.unexpected'): UiMessage {
    if (!body || Array.isArray(body) || typeof body !== 'object') {
        return { i18n: { key: 'error.invalid_response', params: {} } }
    }
    const metadata = body.i18n as MessageDescriptor | undefined
    const valid = metadata && typeof metadata.key === 'string' && metadata.params && typeof metadata.params === 'object'
    const text = typeof body.detail === 'string' ? body.detail : typeof body.message === 'string' ? body.message : typeof body.error === 'string' ? body.error : ''
    const legacy = legacyMessage(text)
    const details = Array.isArray(body.detail) ? body.detail.filter(item => item && typeof item === 'object').map(item => ({ loc: item.loc || [], msg: item.msg || '', i18n: item.i18n })) : undefined
    const unknown = valid && !serverKeys.has(metadata.key)
    const diagnostic = typeof body.diagnostic === 'string' ? body.diagnostic : unknown || !valid && !legacy && !details ? text || JSON.stringify(metadata) || '' : ''
    const reasons = details?.filter(item => !item.i18n || !serverKeys.has(item.i18n.key)).map(item => item.msg) || []
    return {
        ...(valid ? { i18n: metadata } : legacy || { i18n: { key: details ? 'error.validation' : fallback, params: {} } }),
        key: unknown ? `server.${fallback}` : undefined,
        diagnostic: sanitizeDiagnostic([diagnostic, ...reasons].filter(Boolean).join('\n')) || undefined,
        details,
    }
}

/** Partial uploads keep successful files and expose each failed file separately. */
export function uploadMessage(key: string, result: { success_count: number; failed_count: number; failed_files: (UiMessage & { filename: string; error: string })[] }): UiMessage {
    return {
        key,
        params: { success: result.success_count, failed: result.failed_count },
        details: result.failed_files.map(file => ({ loc: [file.filename], msg: file.error, i18n: file.i18n || legacyMessage(file.error)?.i18n || undefined })),
        diagnostic: result.failed_files.filter(file => file.diagnostic).map(file => `${file.filename}: ${sanitizeDiagnostic(file.diagnostic!)}`).join('\n') || undefined,
    }
}
