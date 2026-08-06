import { useEffect, useRef, useState, useCallback } from "react";
import type { WsMessage } from "../api/types";
import { sharedWebSocket } from "../api/websocket";

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
  const listenersRef = useRef<Set<WsListener>>(new Set());

  const subscribe = useCallback((listener: WsListener) => {
    listenersRef.current.add(listener);
    return () => {
      listenersRef.current.delete(listener);
    };
  }, []);

  useEffect(() => {
    const unsubscribeConnection =
      sharedWebSocket.subscribeConnection(setIsConnected);
    const unsubscribeMessage = sharedWebSocket.subscribe((msg) => {
      listenersRef.current.forEach((listener) => listener(msg));
      setLastMessage(msg);
    });
    return () => {
      unsubscribeMessage();
      unsubscribeConnection();
    };
  }, []);

  return { isConnected, lastMessage, subscribe };
}
