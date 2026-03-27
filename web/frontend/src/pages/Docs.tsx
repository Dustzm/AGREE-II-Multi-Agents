import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
    ArrowLeft,
    BookOpenText,
    ExternalLink,
    Languages,
    Loader2,
    Menu,
    RefreshCw,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api, type ManualResponse } from '@/services/api'
import { APP_BASE_PATH } from '@/config'
import { cn } from '@/utils/cn'

interface TocItem {
    id: string
    text: string
    level: 2 | 3
}

function slugifyHeading(text: string) {
    return text
        .toLowerCase()
        .trim()
        .replace(/[`*_~]/g, '')
        .replace(/[^\p{L}\p{N}\s-]/gu, '')
        .replace(/\s+/g, '-')
        .replace(/-+/g, '-')
}

function collectText(node: unknown): string {
    if (typeof node === 'string' || typeof node === 'number') {
        return String(node)
    }

    if (Array.isArray(node)) {
        return node.map(collectText).join('')
    }

    if (node && typeof node === 'object' && 'props' in node) {
        const props = (node as { props?: { children?: unknown } }).props
        return collectText(props?.children)
    }

    return ''
}

function buildToc(markdown: string): TocItem[] {
    const counters = new Map<string, number>()
    const items: TocItem[] = []

    for (const line of markdown.split('\n')) {
        const match = /^(##|###)\s+(.+)$/.exec(line.trim())
        if (!match) continue

        const level = match[1].length as 2 | 3
        const text = match[2].trim()
        const baseId = slugifyHeading(text)
        const count = counters.get(baseId) ?? 0
        counters.set(baseId, count + 1)

        items.push({
            id: count === 0 ? baseId : `${baseId}-${count}`,
            text,
            level,
        })
    }

    return items
}

function resolveManualAsset(path: string) {
    if (!path || path.startsWith('#')) return path
    if (/^(https?:|mailto:|tel:|data:)/i.test(path)) return path

    const docsMarker = '/docs/'
    const docsIndex = path.lastIndexOf(docsMarker)
    if (docsIndex >= 0) {
        return api.getManualAssetUrl(path.slice(docsIndex + docsMarker.length))
    }

    if (path.startsWith('/')) {
        const normalized = path.replace(/^\/+/, '')
        return api.getManualAssetUrl(normalized)
    }

    return api.getManualAssetUrl(path.replace(/^\.?\//, ''))
}

export function Docs() {
    const { t, i18n } = useTranslation()
    const lang = i18n.language?.startsWith('zh') ? 'zh-CN' : 'en-US'
    const [manual, setManual] = useState<ManualResponse | null>(null)
    const [isLoading, setIsLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [mobileTocOpen, setMobileTocOpen] = useState(false)
    const [activeHeading, setActiveHeading] = useState<string>('')

    const toc = useMemo(() => (manual?.content ? buildToc(manual.content) : []), [manual?.content])
    const headingRenderCounts = new Map<string, number>()
    const sourceFile = lang === 'en-US' ? 'docs/USER_MANUAL_EN.md' : 'docs/USER_MANUAL.md'

    useEffect(() => {
        let cancelled = false

        const loadManual = async () => {
            setIsLoading(true)
            setError(null)

            try {
                const data = await api.getManual(lang)
                if (!cancelled) {
                    setManual(data)
                }
            } catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err.message : t('docs.errorDescription'))
                }
            } finally {
                if (!cancelled) {
                    setIsLoading(false)
                }
            }
        }

        loadManual()
        return () => {
            cancelled = true
        }
    }, [lang, t])

    useEffect(() => {
        if (!manual?.available || toc.length === 0) {
            setActiveHeading('')
            return
        }

        const headings = toc
            .map(item => document.getElementById(item.id))
            .filter((element): element is HTMLElement => Boolean(element))

        if (headings.length === 0) return

        const observer = new IntersectionObserver(
            entries => {
                const visible = entries
                    .filter(entry => entry.isIntersecting)
                    .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)

                if (visible[0]?.target?.id) {
                    setActiveHeading(visible[0].target.id)
                }
            },
            {
                rootMargin: '-96px 0px -60% 0px',
                threshold: [0, 0.3, 1],
            }
        )

        headings.forEach(heading => observer.observe(heading))
        return () => observer.disconnect()
    }, [manual, toc])

    const markdownComponents: Components = {
        h2: ({ children, ...props }) => {
            const text = collectText(children)
            const baseId = slugifyHeading(text)
            const currentCount = headingRenderCounts.get(baseId) ?? 0
            headingRenderCounts.set(baseId, currentCount + 1)
            const item = toc.find(entry => entry.id === (currentCount === 0 ? baseId : `${baseId}-${currentCount}`))
            return (
                <h2
                    id={item?.id}
                    className="group scroll-mt-28 border-t border-slate-200/70 pt-10 text-2xl font-semibold tracking-tight text-slate-900 first:border-t-0 first:pt-0"
                    {...props}
                >
                    <a href={`#${item?.id}`} className="inline-flex items-center gap-2 no-underline">
                        <span>{children}</span>
                        <ExternalLink className="h-4 w-4 text-slate-300 opacity-0 transition group-hover:opacity-100" />
                    </a>
                </h2>
            )
        },
        h3: ({ children, ...props }) => {
            const text = collectText(children)
            const baseId = slugifyHeading(text)
            const currentCount = headingRenderCounts.get(baseId) ?? 0
            headingRenderCounts.set(baseId, currentCount + 1)
            const item = toc.find(entry => entry.id === (currentCount === 0 ? baseId : `${baseId}-${currentCount}`))
            return (
                <h3
                    id={item?.id}
                    className="group scroll-mt-28 text-xl font-semibold text-slate-900"
                    {...props}
                >
                    <a href={`#${item?.id}`} className="inline-flex items-center gap-2 no-underline">
                        <span>{children}</span>
                        <ExternalLink className="h-4 w-4 text-slate-300 opacity-0 transition group-hover:opacity-100" />
                    </a>
                </h3>
            )
        },
        img: ({ src = '', alt = '' }) => (
            <img
                src={resolveManualAsset(src)}
                alt={alt}
                className="my-8 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
                loading="lazy"
            />
        ),
        a: ({ href = '', children, ...props }) => {
            const resolvedHref = resolveManualAsset(href)
            const isAnchor = resolvedHref.startsWith('#')
            const isExternal = /^(https?:|mailto:|tel:)/i.test(resolvedHref)

            return (
                <a
                    href={resolvedHref}
                    target={isExternal ? '_blank' : undefined}
                    rel={isExternal ? 'noreferrer' : undefined}
                    className="font-medium text-sky-700 underline decoration-sky-200 underline-offset-4 hover:text-sky-900"
                    {...props}
                >
                    {children}
                    {!isAnchor && isExternal ? <ExternalLink className="ml-1 inline h-3.5 w-3.5" /> : null}
                </a>
            )
        },
        table: ({ children, ...props }) => (
            <div className="my-6 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
                <table className="min-w-full text-sm" {...props}>
                    {children}
                </table>
            </div>
        ),
        th: ({ children, ...props }) => (
            <th className="border-b border-slate-200 bg-slate-50 px-4 py-3 text-left font-semibold text-slate-700" {...props}>
                {children}
            </th>
        ),
        td: ({ children, ...props }) => (
            <td className="border-b border-slate-100 px-4 py-3 align-top text-slate-600 last:border-b-0" {...props}>
                {children}
            </td>
        ),
        code: ({ className, children, ...props }) => {
            const isBlock = className?.includes('language-')
            const content = collectText(children).replace(/^`+|`+$/g, '')
            return (
                <code
                    className={cn(
                        isBlock
                            ? className
                            : 'rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[0.92em] text-slate-800',
                        className
                    )}
                    {...props}
                >
                    {content}
                </code>
            )
        },
        pre: ({ children, ...props }) => (
            <pre
                className="my-6 overflow-x-auto rounded-2xl border border-slate-200 bg-slate-950 px-5 py-4 text-sm text-slate-100 shadow-sm"
                {...props}
            >
                {children}
            </pre>
        ),
        blockquote: ({ children, ...props }) => (
            <blockquote
                className="my-6 rounded-r-2xl border-l-4 border-sky-300 bg-sky-50/70 px-5 py-4 text-slate-700"
                {...props}
            >
                {children}
            </blockquote>
        ),
    }

    const renderBody = () => {
        if (isLoading) {
            return (
                <div className="flex min-h-[320px] items-center justify-center rounded-[28px] border border-slate-200 bg-white/80 shadow-sm">
                    <div className="flex items-center gap-3 text-slate-600">
                        <Loader2 className="h-5 w-5 animate-spin" />
                        <span>{t('docs.loading')}</span>
                    </div>
                </div>
            )
        }

        if (error) {
            return (
                <div className="rounded-[28px] border border-red-200 bg-white p-8 shadow-sm">
                    <h2 className="text-2xl font-semibold text-slate-900">{t('docs.errorTitle')}</h2>
                    <p className="mt-3 text-slate-600">{error || t('docs.errorDescription')}</p>
                    <Button className="mt-6" onClick={() => window.location.reload()}>
                        <RefreshCw className="h-4 w-4" />
                        {t('docs.retry')}
                    </Button>
                </div>
            )
        }

        if (!manual?.available) {
            return (
                <div className="rounded-[28px] border border-amber-200 bg-white p-8 shadow-sm">
                    <span className="inline-flex rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-amber-700">
                        EN
                    </span>
                    <h2 className="mt-4 text-3xl font-semibold text-slate-900">{t('docs.notAvailableTitle')}</h2>
                    <p className="mt-3 max-w-2xl text-slate-600">{t('docs.notAvailableDescription')}</p>
                    <Button className="mt-6" onClick={() => i18n.changeLanguage('zh-CN')}>
                        <Languages className="h-4 w-4" />
                        {t('docs.switchToChinese')}
                    </Button>
                </div>
            )
        }

        if (!manual.content.trim()) {
            return (
                <div className="rounded-[28px] border border-slate-200 bg-white p-8 shadow-sm">
                    <h2 className="text-2xl font-semibold text-slate-900">{t('docs.emptyTitle')}</h2>
                    <p className="mt-3 text-slate-600">{t('docs.emptyDescription')}</p>
                </div>
            )
        }

        return (
            <article className="rounded-[28px] border border-slate-200/80 bg-white px-6 py-8 shadow-[0_20px_60px_-40px_rgba(15,23,42,0.35)] md:px-10 md:py-10">
                <div className="prose prose-slate max-w-none prose-headings:font-semibold prose-p:text-slate-600 prose-p:leading-8 prose-li:text-slate-600 prose-strong:text-slate-900 prose-hr:my-8 prose-hr:border-slate-200 prose-ul:space-y-2 prose-ol:space-y-2">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                        {manual.content}
                    </ReactMarkdown>
                </div>
            </article>
        )
    }

    return (
        <div className="min-h-screen bg-[linear-gradient(180deg,#f8fafc_0%,#eef4ff_45%,#f8fafc_100%)] text-slate-900">
            <header className="sticky top-0 z-30 border-b border-white/80 bg-white/85 backdrop-blur-xl">
                <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
                    <div className="flex min-w-0 items-center gap-3">
                        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-900 text-white shadow-sm">
                            <BookOpenText className="h-5 w-5" />
                        </div>
                        <div className="min-w-0">
                            <h1 className="truncate text-lg font-semibold text-slate-900">{manual?.title || t('docs.brand')}</h1>
                        </div>
                    </div>

                    <div className="flex items-center gap-2 sm:gap-3">
                        <div className="hidden rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm text-slate-600 sm:flex sm:items-center sm:gap-2">
                            <Languages className="h-4 w-4 text-slate-400" />
                            <span>{t('docs.language')}:</span>
                            <div className="inline-flex items-center rounded-full bg-white p-0.5 shadow-sm">
                                <button
                                    type="button"
                                    onClick={() => i18n.changeLanguage('zh-CN')}
                                    className={cn(
                                        'rounded-full px-3 py-1 text-xs font-semibold transition-colors',
                                        lang === 'zh-CN' ? 'bg-slate-900 text-white' : 'text-slate-500 hover:text-slate-900'
                                    )}
                                >
                                    {t('docs.langZh')}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => i18n.changeLanguage('en-US')}
                                    className={cn(
                                        'rounded-full px-3 py-1 text-xs font-semibold transition-colors',
                                        lang === 'en-US' ? 'bg-slate-900 text-white' : 'text-slate-500 hover:text-slate-900'
                                    )}
                                >
                                    {t('docs.langEn')}
                                </button>
                            </div>
                        </div>
                        <Button
                            variant="outline"
                            size="sm"
                            className="lg:hidden"
                            onClick={() => setMobileTocOpen(value => !value)}
                        >
                            <Menu className="h-4 w-4" />
                            {t('docs.mobileToc')}
                        </Button>
                        <Button variant="outline" size="sm" asChild>
                            <Link to="/chat">
                                <ArrowLeft className="h-4 w-4" />
                                {t('docs.backToAma')}
                            </Link>
                        </Button>
                    </div>
                </div>
            </header>

            <div className="mx-auto grid max-w-7xl gap-8 px-4 py-8 sm:px-6 lg:grid-cols-[240px_minmax(0,1fr)] lg:px-8">
                <aside
                    className={cn(
                        'lg:sticky lg:top-28 lg:block lg:h-[calc(100vh-8rem)] lg:self-start',
                        mobileTocOpen ? 'block' : 'hidden'
                    )}
                >
                    <div className="rounded-[24px] border border-slate-200/80 bg-white/90 p-5 shadow-sm">
                        <div className="mb-4 flex items-center justify-between gap-3">
                            <div>
                                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t('docs.brand')}</p>
                                <h2 className="mt-1 text-sm font-semibold text-slate-900">{t('docs.toc')}</h2>
                            </div>
                            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-500">
                                {toc.length}
                            </span>
                        </div>

                        <nav className="max-h-[60vh] space-y-1 overflow-y-auto pr-1 lg:max-h-[calc(100vh-14rem)]">
                            {toc.length > 0 ? (
                                toc.map(item => (
                                    <a
                                        key={item.id}
                                        href={`#${item.id}`}
                                        onClick={() => setMobileTocOpen(false)}
                                        className={cn(
                                            'block rounded-xl px-3 py-2 text-sm transition-colors',
                                            item.level === 3 ? 'ml-3 text-slate-500' : 'font-medium text-slate-700',
                                            activeHeading === item.id
                                                ? 'bg-sky-50 text-sky-700'
                                                : 'hover:bg-slate-50 hover:text-slate-900'
                                        )}
                                    >
                                        {item.text}
                                    </a>
                                ))
                            ) : (
                                <p className="text-sm text-slate-500">
                                    {manual?.available === false ? t('docs.notAvailableDescription') : t('docs.emptyDescription')}
                                </p>
                            )}
                        </nav>
                    </div>
                </aside>

                <main className="min-w-0">
                    <div className="mb-6 flex flex-wrap items-center justify-between gap-4 rounded-[28px] border border-slate-200/80 bg-white/75 px-5 py-4 shadow-sm">
                        <div>
                            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t('docs.brand')}</p>
                            <p className="mt-1 text-sm text-slate-600">{t('docs.sourceLabel')}: {sourceFile}</p>
                        </div>
                        <div className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm text-slate-600">
                            <Languages className="ml-1 h-4 w-4 text-slate-400" />
                            <button
                                type="button"
                                onClick={() => i18n.changeLanguage('zh-CN')}
                                className={cn(
                                    'rounded-full px-3 py-1 text-xs font-semibold transition-colors',
                                    lang === 'zh-CN' ? 'bg-slate-900 text-white' : 'text-slate-500 hover:text-slate-900'
                                )}
                            >
                                {t('docs.langZh')}
                            </button>
                            <button
                                type="button"
                                onClick={() => i18n.changeLanguage('en-US')}
                                className={cn(
                                    'rounded-full px-3 py-1 text-xs font-semibold transition-colors',
                                    lang === 'en-US' ? 'bg-slate-900 text-white' : 'text-slate-500 hover:text-slate-900'
                                )}
                            >
                                {t('docs.langEn')}
                            </button>
                        </div>
                    </div>

                    {renderBody()}
                </main>
            </div>

            <footer className="border-t border-white/70 bg-white/70">
                <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-6 text-sm text-slate-500 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
                    <span>AMA Docs</span>
                    <a
                        href={`${APP_BASE_PATH}/api-docs`}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-2 font-medium text-slate-600 transition-colors hover:text-slate-900"
                    >
                        API Docs
                        <ExternalLink className="h-4 w-4" />
                    </a>
                </div>
            </footer>
        </div>
    )
}
