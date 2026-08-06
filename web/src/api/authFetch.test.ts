import { afterEach, describe, expect, it, vi } from "vitest";
import type { AxiosRequestConfig } from "axios";
import apiClient from "./client";
import {
  authFetch,
  refreshAuthSession,
  resetAuthRefreshForTests,
  subscribeAuthExpired,
} from "./authFetch";

class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = [];

  readonly name: string;
  onmessage: ((event: MessageEvent) => void) | null = null;
  postMessage = vi.fn();
  close = vi.fn();

  constructor(name: string) {
    this.name = name;
    FakeBroadcastChannel.instances.push(this);
  }

  emit(data: unknown): void {
    this.onmessage?.({ data } as MessageEvent);
  }
}

describe("browser authentication transport", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    resetAuthRefreshForTests();
    FakeBroadcastChannel.instances = [];
  });

  it("uses credentialed requests and shares one refresh across concurrent 401s", async () => {
    let apiCalls = 0;
    let refreshCalls = 0;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        expect(init?.credentials).toBe("include");
        if (url === "/api/auth/refresh") {
          refreshCalls += 1;
          await Promise.resolve();
          return new Response("{}", { status: 200 });
        }
        apiCalls += 1;
        return new Response("{}", { status: apiCalls <= 2 ? 401 : 200 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const [first, second] = await Promise.all([
      authFetch("/api/memory/one"),
      authFetch("/api/providers"),
    ]);

    expect(first.status).toBe(200);
    expect(second.status).toBe(200);
    expect(refreshCalls).toBe(1);
    expect(apiCalls).toBe(4);
  });

  it("configures axios to send the HttpOnly session cookie", () => {
    expect(apiClient.defaults.withCredentials).toBe(true);
  });

  it("reports expiry when an axios request cannot refresh its session", async () => {
    const expired = vi.fn();
    const unsubscribe = subscribeAuthExpired(expired);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 401 })),
    );

    await expect(
      apiClient.get("/private", {
        silentCodes: [401],
        adapter: async (config) =>
          Promise.reject({
            config,
            message: "Unauthorized",
            response: { status: 401, data: {} },
          }),
      } as AxiosRequestConfig & { silentCodes: number[] }),
    ).rejects.toBeDefined();

    expect(expired).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("reports one expiry when refresh fails without retrying the original request", async () => {
    const expired = vi.fn();
    const unsubscribe = subscribeAuthExpired(expired);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await authFetch("/api/private");

    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(expired).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("does not refresh or report expiry for a Request targeting session login", async () => {
    const expired = vi.fn();
    const unsubscribe = subscribeAuthExpired(expired);
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await authFetch(
      new Request("https://tianshu.example.com/api/auth/session", {
        method: "POST",
      }),
    );

    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(expired).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("refreshes an expired access cookie before protected session logout", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(new Response("{}", { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await authFetch("/api/auth/session", { method: "DELETE" });

    expect(response.status).toBe(204);
    expect(
      fetchMock.mock.calls.map(([, init]) => init?.method ?? "GET"),
    ).toEqual(["DELETE", "POST", "DELETE"]);
  });

  it("serializes cross-tab refresh and skips rotation when the cookie is already fresh", async () => {
    const requestLock = vi.fn(
      async (_name: string, callback: () => Promise<boolean>) => callback(),
    );
    vi.stubGlobal("window", {});
    vi.stubGlobal("navigator", { locks: { request: requestLock } });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/auth/me");
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(refreshAuthSession()).resolves.toBe(true);

    expect(requestLock).toHaveBeenCalledTimes(1);
    expect(requestLock.mock.calls[0]?.[0]).toBe("tianshu-auth-refresh");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("propagates session invalidation from another browser tab", () => {
    vi.stubGlobal("window", {});
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    const expired = vi.fn();
    const unsubscribe = subscribeAuthExpired(expired);

    expect(FakeBroadcastChannel.instances).toHaveLength(1);
    FakeBroadcastChannel.instances[0]!.emit({
      type: "session-invalidated",
      source: "another-tab",
    });

    expect(expired).toHaveBeenCalledTimes(1);
    unsubscribe();
  });
});
