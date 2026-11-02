import { createElement } from 'react'
import toast from 'react-hot-toast'
import { MessageView } from '@/components/MessageView'
import { asMessage } from './messages'

export function notifyError(error: unknown, fallback = 'common.error.unknown') {
    return toast.error(createElement(MessageView, { message: asMessage(error, fallback) }), { duration: 8000 })
}

export function notifySuccess(key: string, params: Record<string, unknown> = {}) {
    return toast.success(createElement(MessageView, { message: { key, params } }))
}
