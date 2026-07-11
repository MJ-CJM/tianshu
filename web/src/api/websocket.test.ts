import { describe, expect, it, vi } from "vitest";
import { WebSocketManager, type ManagedWebSocket } from "./websocket";

class FakeSocket implements ManagedWebSocket {
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();
}

describe("shared WebSocket manager", () => {
  it("shares one cookie-authenticated URL across subscribers", () => {
    const sockets: FakeSocket[] = [];
    const createSocket = vi.fn((_url: string) => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    });
    const manager = new WebSocketManager({
      getUrl: () => "wss://tianshu.example.com/api/ws",
      createSocket,
      refreshSession: async () => true,
    });

    const first = manager.subscribeConnection(() => undefined);
    const second = manager.subscribeConnection(() => undefined);

    expect(createSocket).toHaveBeenCalledTimes(1);
    expect(createSocket).toHaveBeenCalledWith("wss://tianshu.example.com/api/ws");
    expect(createSocket.mock.calls[0]![0]).not.toContain("token=");
    first();
    expect(sockets[0]!.close).not.toHaveBeenCalled();
    second();
    expect(sockets[0]!.close).toHaveBeenCalledTimes(1);
  });

  it("refreshes once on 4401 and permanently stops on 4403", async () => {
    const sockets: FakeSocket[] = [];
    const refreshSession = vi.fn(async () => true);
    const manager = new WebSocketManager({
      getUrl: () => "wss://tianshu.example.com/api/ws",
      createSocket: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket;
      },
      refreshSession,
      schedule: (callback) => {
        callback();
        return 1 as unknown as ReturnType<typeof setTimeout>;
      },
      cancelSchedule: () => undefined,
    });
    manager.subscribeConnection(() => undefined);

    sockets[0]!.onclose?.({ code: 4401 } as CloseEvent);
    await Promise.resolve();
    await Promise.resolve();

    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(sockets).toHaveLength(2);
    sockets[1]!.onclose?.({ code: 4403 } as CloseEvent);
    await Promise.resolve();
    expect(sockets).toHaveLength(2);
  });
});
