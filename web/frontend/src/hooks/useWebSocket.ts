import { useCallback, useRef, useState } from 'react'
import { WS_URL } from '@/config'

export interface ChatMessage {
    type: 'status' | 'agent' | 'progress' | 'complete' | 'error'
    content?: string
    agent?: string
    progress?: number
    total?: number
    task_id?: string
    prompt?: string  // Appraiser 的提示词内容
}

interface UseWebSocketOptions {
    onMessage?: (message: ChatMessage) => void
    onError?: (error: Event) => void
    onClose?: () => void
    onOpen?: () => void
}

export function useWebSocket(options: UseWebSocketOptions = {}) {
    const [isConnected, setIsConnected] = useState(false)
    const wsRef = useRef<WebSocket | null>(null)
    const pendingMessages = useRef<object[]>([])

    const connect = useCallback((sessionId: string) => {
        // 如果已连接，先关闭
        if (wsRef.current) {
            wsRef.current.close()
        }

        const ws = new WebSocket(`${WS_URL}/chat/${sessionId}`)

        ws.onopen = () => {
            console.log('WebSocket 已连接')
            setIsConnected(true)
            options.onOpen?.()

            // 发送待发送的消息
            while (pendingMessages.current.length > 0) {
                const msg = pendingMessages.current.shift()
                if (msg) {
                    ws.send(JSON.stringify(msg))
                }
            }
        }

        ws.onmessage = (event) => {
            const message = JSON.parse(event.data) as ChatMessage
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
            options.onClose?.()
        }

        wsRef.current = ws
    }, [options])

    const disconnect = useCallback(() => {
        wsRef.current?.close()
        wsRef.current = null
        setIsConnected(false)
    }, [])

    const send = useCallback((data: object) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify(data))
        } else {
            // 连接未就绪，加入待发送队列
            console.log('WebSocket 未就绪，消息加入队列')
            pendingMessages.current.push(data)
        }
    }, [])

    return {
        isConnected,
        connect,
        disconnect,
        send,
    }
}
