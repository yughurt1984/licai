import { useEffect, useRef, useCallback } from 'react'

const AUTH_EVENT = 'licai:auth-changed'

/**
 * 实时行情推送。enabled=false(未登录)不建立连接; 服务端会话失效时以 4401 关闭,
 * 此时停止重连并广播 auth 事件(App 切登录页), 登录成功后 enabled 翻 true 重连。
 */
export function useWebSocket(onMessage, enabled = true) {
  const wsRef = useRef(null)
  const stoppedRef = useRef(false)
  const reconnectDelay = useRef(1000)
  const onMessageRef = useRef(onMessage)
  const connectRef = useRef(null)      // 重连定时器回调用: 避免在 connect 自身闭包里引用自己

  const connect = useCallback(() => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${location.host}/ws`)

    ws.onopen = () => { reconnectDelay.current = 1000 }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type !== 'heartbeat' && msg.type !== 'pong') {
          onMessageRef.current?.(msg)
        }
      } catch {}
    }

    ws.onclose = (e) => {
      if (e.code === 4401) {
        // 会话无效: 无限重连没有意义, 停下并把登录页叫出来
        stoppedRef.current = true
        try { window.dispatchEvent(new CustomEvent(AUTH_EVENT)) } catch {}
        return
      }
      if (stoppedRef.current) return
      setTimeout(() => connectRef.current?.(), reconnectDelay.current)
      reconnectDelay.current = Math.min(reconnectDelay.current * 2, 30000)
    }

    ws.onerror = () => ws.close()

    // Keepalive
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, 25000)

    wsRef.current = ws
    return () => { clearInterval(ping) }
  }, [])

  // ref 赋值放 effect(渲染期不许碰 .current, react-hooks/refs)
  useEffect(() => {
    onMessageRef.current = onMessage
    connectRef.current = connect
  })

  useEffect(() => {
    if (!enabled) {
      stoppedRef.current = true
      wsRef.current?.close()
      wsRef.current = null
      return
    }
    stoppedRef.current = false
    const cleanup = connect()
    return () => {
      cleanup?.()
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [connect, enabled])
}
