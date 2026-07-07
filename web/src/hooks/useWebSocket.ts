import { useEffect, useRef, useState, useCallback } from "react";
import type { WsMessage } from "../api/types";

function getWsUrl(): string {
  const loc = window.location;
  const protocol = loc.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${loc.host}/api/ws`;
}

export type WsListener = (msg: WsMessage) => void;

export function useWebSocket(): {
  isConnected: boolean;
  lastMessage: WsMessage | null;
  /** 注册同步消息监听器；返回取消函数。
   *
   * 用于解决 React 18 自动批处理可能丢消息的场景：相邻两条 WS 消息
   * 间隔很短时，setState 单值会被合并/覆盖，导致 useEffect 跑不到中间消息。
   * 用 subscribe 注册的回调在 ws.onmessage 同步触发，每条消息都会被处理。
   */
  subscribe: (listener: WsListener) => () => void;
} {
  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const listenersRef = useRef<Set<WsListener>>(new Set());

  const subscribe = useCallback((listener: WsListener) => {
    listenersRef.current.add(listener);
    return () => {
      listenersRef.current.delete(listener);
    };
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    const ws = new WebSocket(getWsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setIsConnected(true);
      retryCountRef.current = 0;
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const msg: WsMessage = JSON.parse(event.data);
        // 同步派发给所有 listener — 不走 React state，避免批处理丢消息
        listenersRef.current.forEach((l) => {
          try {
            l(msg);
          } catch (err) {
            // 单个 listener 异常不影响其他 listener
            // eslint-disable-next-line no-console
            console.error("ws listener error:", err);
          }
        });
        setLastMessage(msg);
      } catch {
        // ignore non-JSON messages
      }
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setIsConnected(false);
      // exponential backoff: 1s, 2s, 4s, ..., 30s max
      const delay = Math.min(1000 * 2 ** retryCountRef.current, 30_000);
      retryCountRef.current += 1;
      timerRef.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [connect]);

  return { isConnected, lastMessage, subscribe };
}
