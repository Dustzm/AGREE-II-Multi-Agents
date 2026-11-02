import type { ChatMessage } from '@/hooks/useWebSocket'
import type { TimelineEvent } from '@/components/TimelineView'
import { asMessage, legacyMessage, type UiMessage } from './messages'

const phases = new Set(['parsing', 'preprocessing', 'orchestrator', 'initiator', 'appraiser', 'reporter'])

/** Historical text is adapted here; current events always use phase and state. */
export function taskEvent(message: ChatMessage): { id: string; type: TimelineEvent['type']; status: TimelineEvent['status']; message: UiMessage } | null {
    if (message.phase && phases.has(message.phase)) {
        const type = message.phase as TimelineEvent['type']
        const status = message.state === 'done' || message.state === 'error' ? message.state : 'running'
        return { id: message.skill_id ? `appraiser_${message.skill_id}` : type, type, status, message: message.i18n ? message : asMessage(message.content, 'chat.status.processing') }
    }
    const data = legacyMessage(message.content || '')
    const key = data?.i18n?.key || ''
    const type = /parse|mineru|doc_found/.test(key) ? 'parsing' : /evidence|preprocess/.test(key) ? 'preprocessing' : message.agent?.toLowerCase()
    if (!type || !phases.has(type)) return null
    const status = /done|reusing|doc_found|completed/.test(key) ? 'done' : /failed|error/.test(key) ? 'error' : 'running'
    return { id: type, type: type as TimelineEvent['type'], status, message: data || asMessage(message.content, 'chat.status.processing') }
}
