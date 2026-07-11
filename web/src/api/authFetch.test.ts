import { afterEach, describe, expect, it, vi } from "vitest";
import apiClient from "./client";
import { authFetch, resetAuthRefreshForTests } from "./authFetch";

describe("browser authentication transport", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    resetAuthRefreshForTests();
  });

  it("uses credentialed requests and shares one refresh across concurrent 401s", async () => {
    let apiCalls = 0;
    let refreshCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      expect(init?.credentials).toBe("include");
      if (url === "/api/auth/refresh") {
        refreshCalls += 1;
        await Promise.resolve();
        return new Response("{}", { status: 200 });
      }
      apiCalls += 1;
      return new Response("{}", { status: apiCalls <= 2 ? 401 : 200 });
    });
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
});
