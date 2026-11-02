import { useTranslation } from 'react-i18next'
import { asMessage, renderMessage, type UiMessage } from '@/i18n/messages'

interface MessageViewProps {
    message: UiMessage | Error | string
    fallback?: string
}

export function MessageView({ message, fallback = 'common.error.unknown' }: MessageViewProps) {
    const { t, i18n } = useTranslation()
    const data = asMessage(message, fallback)

    return (
        <div className="min-w-0 text-sm" role="status">
            <span>{renderMessage(data, t, fallback, i18n.language)}</span>
            {data.details && data.details.length > 0 && (
                <ul className="mt-1 list-inside list-disc">
                    {data.details.map((detail, index) => (
                        <li key={index}>
                            {detail.loc.filter(part => part !== 'body').join('.')}: {renderMessage({ i18n: detail.i18n }, t, 'server.validation.invalid')}
                        </li>
                    ))}
                </ul>
            )}
            {data.diagnostic && (
                <details className="mt-2 text-xs font-normal">
                    <summary className="cursor-pointer">{t('common.diagnosticDetails')}</summary>
                    <pre className="mt-1 max-h-52 max-w-full overflow-auto whitespace-pre-wrap break-all rounded bg-black/5 p-2">{data.diagnostic}</pre>
                </details>
            )}
        </div>
    )
}
