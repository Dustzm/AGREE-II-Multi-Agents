import { useCallback, useEffect, useRef, useState } from 'react'
import { WS_URL } from '@/config'

export interface ChatMessage {
    type: 'status' | 'agent' | 'progress' | 'complete' | 'error' | 'item_progress' | 'batch_summary' | 'pong'
    content?: string
    agent?: string
    progress?: number
    total?: number
    task_id?: string
    prompt?: string
    skill_id?: string
    title?: string
    item_status?: 'started' | 'completed' | 'failed'
    duration?: number
    completed_count?: number
    elapsed?: number
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
    const maxReconnectAttempts = 5

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

    const scheduleReconnect = useCallback((sessionId: string, attempt: number) => {
        if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current)
        }
        const delay = Math.min(1000 * Math.pow(2, attempt), 30000)
        console.log(`将在 ${delay}ms 后尝试重连 (尝试 ${attempt}/${maxReconnectAttempts})`)
        reconnectTimeoutRef.current = setTimeout(() => {
            if (attempt < maxReconnectAttempts) {
                setReconnectAttempts(attempt + 1)
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
        if (wsRef.current) {
            wsRef.current.close()
        }

        sessionIdRef.current = sessionId

        const ws = new WebSocket(`${WS_URL}/chat/${sessionId}`)

        ws.onopen = () => {
            console.log('WebSocket 已连接')
            setIsConnected(true)
            setReconnectAttempts(0)
            stopReconnect()
            options.onOpen?.()
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
            options.onMessage?.(message)
        }

        ws.onerror = (error) => {
            console.error('WebSocket 错误:', error)
            options.onError?.(error)
        }

        ws.onclose = () => {
            console.log('WebSocket 已断开')
            setIsConnected(false)
            stopHeartbeat()
            options.onClose?.()

            if (sessionIdRef.current && reconnectAttempts < maxReconnectAttempts) {
                scheduleReconnect(sessionIdRef.current, reconnectAttempts + 1)
            }
        }

        wsRef.current = ws
    }, [options, startHeartbeat, stopHeartbeat, stopReconnect, scheduleReconnect, reconnectAttempts])

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
        stopReconnect()
        stopHeartbeat()
        wsRef.current?.close()
        wsRef.current = null
        sessionIdRef.current = null
        setIsConnected(false)
    }, [stopReconnect, stopHeartbeat])

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
