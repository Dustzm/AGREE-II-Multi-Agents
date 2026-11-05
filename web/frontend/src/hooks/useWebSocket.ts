import type { MessageDescriptor, UiMessage } from '@/i18n/messages'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createAuthenticatedWebSocket, isUnauthorizedClose, notifyUnauthorized } from '@/services/http'

export interface ChatMessage {
    type: 'status' | 'agent' | 'progress' | 'complete' | 'partial' | 'cancelled' | 'error' | 'item_progress' | 'batch_summary' | 'session_state' | 'pong'
    content?: string
    i18n?: MessageDescriptor
    diagnostic?: string
    phase?: string
    // session_state 快照额外使用 idle / starting
    state?: 'idle' | 'starting' | 'running' | 'done' | 'error' | 'stopping'
    agent?: string
    progress?: number
    total?: number
    task_id?: string | null
    prompt?: string
    skill_id?: string
    title?: string
    item_status?: 'started' | 'completed' | 'failed'
    duration?: number
    completed_count?: number
    elapsed?: number
    missing_items?: string[]
    failed_details?: Record<string, UiMessage & { type: string; stage: string; message: string }>
}

interface UseWebSocketOptions {
    onMessage?: (message: ChatMessage) => void
    onError?: (error: Event) => void
    onClose?: () => void
    onOpen?: () => void
}

export function useWebSocket(options: UseWebSocketOptions = {}) {
    const [isConnected, setIsConnected] = useState(false)
    const [reconnectAttempts, setReconnectAttempts] = useState(0)
    const wsRef = useRef<WebSocket | null>(null)
    const pendingMessages = useRef<object[]>([])
    const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)
    const heartbeatIntervalRef = useRef<NodeJS.Timeout | null>(null)
    const sessionIdRef = useRef<string | null>(null)
    const optionsRef = useRef(options)
    const reconnectAttemptsRef = useRef(0)
    const allowReconnectRef = useRef(true)
    const connectRef = useRef<(sessionId: string) => void>(() => undefined)
    const maxReconnectAttempts = 5

    useEffect(() => {
        optionsRef.current = options
    }, [options])

    useEffect(() => {
        reconnectAttemptsRef.current = reconnectAttempts
    }, [reconnectAttempts])

    const startHeartbeat = useCallback(() => {
        if (heartbeatIntervalRef.current) {
            clearInterval(heartbeatIntervalRef.current)
        }
        heartbeatIntervalRef.current = setInterval(() => {
            if (wsRef.current?.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({ type: 'ping' }))
            }
        }, 30000)
    }, [])

    const stopHeartbeat = useCallback(() => {
        if (heartbeatIntervalRef.current) {
            clearInterval(heartbeatIntervalRef.current)
            heartbeatIntervalRef.current = null
        }
    }, [])

    const scheduleReconnect = useCallback((attempt: number) => {
        if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current)
        }
        const delay = Math.min(1000 * Math.pow(2, attempt), 30000)
        console.log(`将在 ${delay}ms 后尝试重连 (尝试 ${attempt}/${maxReconnectAttempts})`)
        setReconnectAttempts(attempt)
        reconnectTimeoutRef.current = setTimeout(() => {
            if (!allowReconnectRef.current || !sessionIdRef.current) {
                return
            }

            if (attempt <= maxReconnectAttempts) {
                connectRef.current(sessionIdRef.current)
            } else {
                console.log('已达到最大重连次数，停止重连')
                setReconnectAttempts(0)
            }
        }, delay)
    }, [maxReconnectAttempts])

    const stopReconnect = useCallback(() => {
        if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current)
            reconnectTimeoutRef.current = null
        }
        setReconnectAttempts(0)
    }, [])

    const connect = useCallback((sessionId: string) => {
        const current = wsRef.current
        if (
            current &&
            sessionIdRef.current === sessionId &&
            (current.readyState === WebSocket.OPEN || current.readyState === WebSocket.CONNECTING)
        ) {
            return
        }

        if (current) {
            allowReconnectRef.current = false
            current.close()
        }

        sessionIdRef.current = sessionId
        allowReconnectRef.current = true

        const ws = createAuthenticatedWebSocket(`/chat/${sessionId}`)

        ws.onopen = () => {
            console.log('WebSocket 已连接')
            setIsConnected(true)
            setReconnectAttempts(0)
            stopReconnect()
            optionsRef.current.onOpen?.()
            startHeartbeat()

            while (pendingMessages.current.length > 0) {
                const msg = pendingMessages.current.shift()
                if (msg) {
                    ws.send(JSON.stringify(msg))
                }
            }
        }

        ws.onmessage = (event) => {
            const message = JSON.parse(event.data) as ChatMessage
            
            if (message.type === 'pong') {
                return
            }
            
            console.log('WebSocket 消息:', message)
            optionsRef.current.onMessage?.(message)
        }

        ws.onerror = (error) => {
            console.error('WebSocket 错误:', error)
            optionsRef.current.onError?.(error)
        }

        ws.onclose = (event) => {
            console.log('WebSocket 已断开')
            setIsConnected(false)
            stopHeartbeat()
            if (wsRef.current === ws) {
                wsRef.current = null
            }
            optionsRef.current.onClose?.()

            if (isUnauthorizedClose(event)) {
                notifyUnauthorized()
                return
            }

            if (allowReconnectRef.current && sessionIdRef.current && reconnectAttemptsRef.current < maxReconnectAttempts) {
                scheduleReconnect(reconnectAttemptsRef.current + 1)
            }
        }

        wsRef.current = ws
    }, [startHeartbeat, stopHeartbeat, stopReconnect, scheduleReconnect, maxReconnectAttempts])

    useEffect(() => {
        connectRef.current = connect
    }, [connect])

    useEffect(() => {
        const handleVisibilityChange = () => {
            if (document.visibilityState === 'visible' && !isConnected && sessionIdRef.current) {
                console.log('页面重新可见，尝试重连')
                connect(sessionIdRef.current)
            }
        }

        document.addEventListener('visibilitychange', handleVisibilityChange)
        return () => {
            document.removeEventListener('visibilitychange', handleVisibilityChange)
        }
    }, [isConnected, connect])

    const disconnect = useCallback(() => {
        allowReconnectRef.current = false
        stopReconnect()
        stopHeartbeat()
        wsRef.current?.close()
        wsRef.current = null
        sessionIdRef.current = null
        setIsConnected(false)
    }, [stopReconnect, stopHeartbeat])

    // 组件卸载时必须断开：否则孤儿连接仍被后端视为在线，离开期间的消息既不会进离线队列，也无人处理
    useEffect(() => disconnect, [disconnect])

    const send = useCallback((data: object) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify(data))
        } else {
            console.log('WebSocket 未就绪，消息加入队列')
            pendingMessages.current.push(data)
        }
    }, [])

    return {
        isConnected,
        reconnectAttempts,
        connect,
        disconnect,
        send,
    }
}
