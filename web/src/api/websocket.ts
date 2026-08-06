import type { WsMessage } from "./types";
import { notifyAuthExpired, refreshAuthSession } from "./authFetch";

export interface ManagedWebSocket {
  readyState: number;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  close(): void;
}

interface WebSocketManagerOptions {
  getUrl: () => string;
  createSocket: (url: string) => ManagedWebSocket;
  refreshSession: () => Promise<boolean>;
  schedule?: (
    callback: () => void,
    delay: number,
  ) => ReturnType<typeof setTimeout>;
  cancelSchedule?: (timer: ReturnType<typeof setTimeout>) => void;
}

type ConnectionListener = (connected: boolean) => void;
type MessageListener = (message: WsMessage) => void;

export class WebSocketManager {
  private readonly options: Required<WebSocketManagerOptions>;
  private readonly connectionListeners = new Set<ConnectionListener>();
  private readonly messageListeners = new Set<MessageListener>();
  private socket: ManagedWebSocket | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private connected = false;
  private retryCount = 0;
  private authRetryAttempted = false;
  private blocked = false;

  constructor(options: WebSocketManagerOptions) {
    this.options = {
      ...options,
      schedule:
        options.schedule ?? ((callback, delay) => setTimeout(callback, delay)),
      cancelSchedule:
        options.cancelSchedule ?? ((timer) => clearTimeout(timer)),
    };
  }

  subscribeConnection(listener: ConnectionListener): () => void {
    this.connectionListeners.add(listener);
    listener(this.connected);
    if (this.connectionListeners.size === 1) {
      this.blocked = false;
      this.connect();
    }
    return () => {
      this.connectionListeners.delete(listener);
      if (this.connectionListeners.size === 0) this.stop();
    };
  }

  subscribe(listener: MessageListener): () => void {
    this.messageListeners.add(listener);
    return () => this.messageListeners.delete(listener);
  }

  private notifyConnection(connected: boolean): void {
    this.connected = connected;
    this.connectionListeners.forEach((listener) => listener(connected));
  }

  private connect(): void {
    if (this.blocked || this.connectionListeners.size === 0 || this.socket)
      return;
    const socket = this.options.createSocket(this.options.getUrl());
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.retryCount = 0;
      this.authRetryAttempted = false;
      this.notifyConnection(true);
    };
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(String(event.data)) as WsMessage;
        this.messageListeners.forEach((listener) => {
          try {
            listener(message);
          } catch (error) {
            // One page listener must not break the shared transport.
            console.error("ws listener error:", error);
          }
        });
      } catch {
        // Ignore non-JSON transport messages.
      }
    };
    socket.onerror = () => socket.close();
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.notifyConnection(false);
      if (this.connectionListeners.size === 0) return;
      if (event.code === 4403) {
        this.blocked = true;
        return;
      }
      if (event.code === 4401) {
        if (this.authRetryAttempted) {
          this.blocked = true;
          notifyAuthExpired();
          return;
        }
        this.authRetryAttempted = true;
        void this.options.refreshSession().then((refreshed) => {
          if (refreshed) this.scheduleReconnect(0);
          else {
            this.blocked = true;
            notifyAuthExpired();
          }
        });
        return;
      }
      const delay = Math.min(1000 * 2 ** this.retryCount, 30_000);
      this.retryCount += 1;
      this.scheduleReconnect(delay);
    };
  }

  private scheduleReconnect(delay: number): void {
    if (this.timer) this.options.cancelSchedule(this.timer);
    this.timer = this.options.schedule(() => {
      this.timer = null;
      this.connect();
    }, delay);
  }

  private stop(): void {
    if (this.timer) {
      this.options.cancelSchedule(this.timer);
      this.timer = null;
    }
    if (this.socket) {
      const socket = this.socket;
      this.socket = null;
      socket.onclose = null;
      socket.close();
    }
    this.notifyConnection(false);
  }
}

function getWsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/ws`;
}

export const sharedWebSocket = new WebSocketManager({
  getUrl: getWsUrl,
  createSocket: (url) => new WebSocket(url),
  refreshSession: refreshAuthSession,
});
