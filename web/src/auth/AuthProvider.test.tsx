// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { notifyAuthExpired, resetAuthRefreshForTests } from "../api/authFetch";
import { useAuth } from "./AuthContext";
import { AuthProvider } from "./AuthProvider";
import LoginGate from "./LoginGate";

const principal = {
  id: "user:owner",
  kind: "human",
  display_name: "Owner",
  scopes: ["api", "admin"],
};

function jsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, { status });
}

function renderGate(
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  }),
) {
  const view = render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <LoginGate>
          <div>protected-content</div>
        </LoginGate>
      </AuthProvider>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

function LogoutProbe() {
  const { logout } = useAuth();
  return <button onClick={() => void logout()}>test-logout</button>;
}

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, String(value));
    },
  };
}

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  vi.stubGlobal("localStorage", memoryStorage());
  vi.stubGlobal("sessionStorage", memoryStorage());
});

afterEach(() => {
  cleanup();
  resetAuthRefreshForTests();
  localStorage.clear();
  sessionStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("browser authentication gate", () => {
  it("keeps trusted-local startup automatic while waiting for identity before mounting pages", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ mode: "trusted-local", login_required: false }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          principal: { ...principal, id: "local:owner", kind: "local" },
          source: "trusted-local",
          client_kind: "web",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    renderGate();

    expect(screen.queryByText("protected-content")).toBeNull();
    expect(await screen.findByText("protected-content")).not.toBeNull();
    expect(screen.queryByLabelText("访问令牌")).toBeNull();
  });

  it("exchanges a PAT once in secure mode without storage or query credentials", async () => {
    const storageSpy = vi.spyOn(localStorage, "setItem");
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url =
          typeof input === "string"
            ? input
            : input instanceof Request
              ? input.url
              : input.toString();
        if (url.endsWith("/api/auth/mode")) {
          return jsonResponse({ mode: "secure-remote", login_required: true });
        }
        if (url.endsWith("/api/auth/me")) return jsonResponse({}, 401);
        if (url.endsWith("/api/auth/refresh")) return jsonResponse({}, 401);
        if (url.endsWith("/api/auth/session") && init?.method === "POST") {
          return jsonResponse({
            principal,
            access_expires_at: "2026-07-11T12:00:00Z",
          });
        }
        return new Response(null, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    renderGate();
    const input = await screen.findByLabelText("访问令牌");
    fireEvent.change(input, { target: { value: "one-time-pat" } });
    fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));

    expect(await screen.findByText("protected-content")).not.toBeNull();
    const loginCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/api/auth/session") && init?.method === "POST",
    );
    expect(loginCall?.[1]).toEqual(
      expect.objectContaining({
        credentials: "include",
        body: JSON.stringify({ token: "one-time-pat" }),
      }),
    );
    expect(
      fetchMock.mock.calls.every(
        ([input]) => !String(input).includes("token="),
      ),
    ).toBe(true);
    expect(storageSpy.mock.calls.flat().join(" ")).not.toContain(
      "one-time-pat",
    );
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("clears cached user data and returns to the secure login gate on logout", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/auth/mode")) {
          return jsonResponse({ mode: "secure-remote", login_required: true });
        }
        if (url.endsWith("/api/auth/me")) {
          return jsonResponse({
            principal,
            source: "session-cookie",
            client_kind: "web",
          });
        }
        if (url.endsWith("/api/auth/session") && init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        return new Response(null, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(["private"], { secret: true });

    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <LoginGate>
            <LogoutProbe />
          </LoginGate>
        </AuthProvider>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "test-logout" }));
    expect(await screen.findByLabelText("访问令牌")).not.toBeNull();
    expect(queryClient.getQueryData(["private"])).toBeUndefined();
  });

  it("returns an authenticated page to the login gate when transports report expiry", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ mode: "secure-remote", login_required: true }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          principal,
          source: "session-cookie",
          client_kind: "web",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    renderGate();
    expect(await screen.findByText("protected-content")).not.toBeNull();

    act(() => notifyAuthExpired());

    expect(await screen.findByLabelText("访问令牌")).not.toBeNull();
  });

  it("shows a generic error for an invalid PAT and never renders the secret", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/auth/mode")) {
          return jsonResponse({ mode: "secure-remote", login_required: true });
        }
        if (url.endsWith("/api/auth/me") || url.endsWith("/api/auth/refresh")) {
          return jsonResponse({}, 401);
        }
        if (url.endsWith("/api/auth/session") && init?.method === "POST") {
          return jsonResponse({ detail: "invalid credentials" }, 401);
        }
        return new Response(null, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    renderGate();
    fireEvent.change(await screen.findByLabelText("访问令牌"), {
      target: { value: "do-not-render-me" },
    });
    fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));

    await waitFor(() =>
      expect(screen.queryByText("凭证无效，请重试")).not.toBeNull(),
    );
    expect(document.body.textContent).not.toContain("do-not-render-me");
    expect((screen.getByLabelText("访问令牌") as HTMLInputElement).value).toBe(
      "",
    );
  });
});
