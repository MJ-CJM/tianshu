import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createAuthSession,
  deleteAuthSession,
  getAuthMe,
  getAuthMode,
} from "./auth";
import {
  resetAuthRefreshForTests,
  subscribeAuthExpired,
} from "./authFetch";

const principal = {
  id: "user:owner",
  kind: "human" as const,
  display_name: "Owner",
  scopes: ["api", "admin"],
};

describe("authentication API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    resetAuthRefreshForTests();
  });

  it("uses same-origin credentialed requests for the complete browser session lifecycle", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/mode") {
        return Response.json({ mode: "secure-remote", login_required: true });
      }
      if (url === "/api/auth/session" && init?.method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      if (url === "/api/auth/session") {
        return Response.json({ principal, access_expires_at: "2026-07-11T12:00:00Z" });
      }
      if (url === "/api/auth/me") {
        return Response.json({ principal, source: "session-cookie", client_kind: "web" });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(getAuthMode()).resolves.toEqual({
      mode: "secure-remote",
      login_required: true,
    });
    await expect(createAuthSession("one-time-pat")).resolves.toMatchObject({ principal });
    await expect(getAuthMe()).resolves.toMatchObject({ principal });
    await expect(deleteAuthSession()).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledTimes(4);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.credentials).toBe("include");
    }
    expect(fetchMock.mock.calls[1]).toEqual([
      "/api/auth/session",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({ token: "one-time-pat" }),
      }),
    ]);
    expect(fetchMock.mock.calls[3]).toEqual([
      "/api/auth/session",
      expect.objectContaining({ method: "DELETE", credentials: "include" }),
    ]);
  });

  it("notifies peer tabs after a successful logout", async () => {
    const expired = vi.fn();
    const unsubscribe = subscribeAuthExpired(expired);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    );

    await deleteAuthSession();

    expect(expired).toHaveBeenCalledTimes(1);
    unsubscribe();
  });
});
