import { useEffect, useRef, useState, useCallback } from "react";
import type { WsMessage } from "../api/types";

function getWsUrl(): string {
  const loc = window.location;
  const protocol = loc.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${loc.host}/api/ws`;
}

export function useWebSocket(): {
  isConnected: boolean;
  lastMessage: WsMessage | null;
} {
  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

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

  return { isConnected, lastMessage };
}
