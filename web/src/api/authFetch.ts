let refreshPromise: Promise<boolean> | null = null;
type AuthExpiredListener = () => void;
const authExpiredListeners = new Set<AuthExpiredListener>();
const AUTH_CHANNEL_NAME = "tianshu-auth";
const AUTH_REFRESH_LOCK_NAME = "tianshu-auth-refresh";
const authTabId =
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
let authChannel: BroadcastChannel | null = null;

interface AuthChannelMessage {
  type: "session-invalidated";
  source: string;
}

function emitAuthExpired(): void {
  authExpiredListeners.forEach((listener) => listener());
}

function ensureAuthChannel(): BroadcastChannel | null {
  if (
    authChannel ||
    typeof window === "undefined" ||
    typeof BroadcastChannel === "undefined"
  ) {
    return authChannel;
  }
  authChannel = new BroadcastChannel(AUTH_CHANNEL_NAME);
  authChannel.onmessage = (event: MessageEvent<unknown>) => {
    const message = event.data as Partial<AuthChannelMessage> | null;
    if (
      message?.type === "session-invalidated" &&
      typeof message.source === "string" &&
      message.source !== authTabId
    ) {
      emitAuthExpired();
    }
  };
  return authChannel;
}

export function subscribeAuthExpired(
  listener: AuthExpiredListener,
): () => void {
  authExpiredListeners.add(listener);
  ensureAuthChannel();
  return () => authExpiredListeners.delete(listener);
}

export function notifyAuthExpired(): void {
  emitAuthExpired();
  ensureAuthChannel()?.postMessage({
    type: "session-invalidated",
    source: authTabId,
  } satisfies AuthChannelMessage);
}

async function rotateAuthSession(): Promise<boolean> {
  return fetch("/api/auth/refresh", {
    method: "POST",
    credentials: "include",
  })
    .then((response) => response.ok)
    .catch(() => false);
}

async function refreshAcrossBrowserTabs(): Promise<boolean> {
  const locks =
    typeof window !== "undefined" && typeof navigator !== "undefined"
      ? navigator.locks
      : undefined;
  if (!locks) return rotateAuthSession();
  return locks.request(AUTH_REFRESH_LOCK_NAME, async () => {
    const currentSession = await fetch("/api/auth/me", {
      credentials: "include",
    });
    if (currentSession.ok) return true;
    return rotateAuthSession();
  });
}

export function refreshAuthSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = refreshAcrossBrowserTabs()
      .catch(() => false)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof Request) return input.url;
  return input.toString();
}

function requestMethod(input: RequestInfo | URL, init: RequestInit): string {
  if (init.method) return init.method.toUpperCase();
  if (input instanceof Request) return input.method.toUpperCase();
  return "GET";
}

function isPublicAuthRequest(
  input: RequestInfo | URL,
  init: RequestInit,
): boolean {
  const path = new URL(requestUrl(input), "http://localhost").pathname;
  const method = requestMethod(input, init);
  return (
    method === "POST" &&
    (path === "/api/auth/refresh" || path === "/api/auth/session")
  );
}

export async function authFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const credentialed = { ...init, credentials: "include" as const };
  let response = await fetch(input, credentialed);
  if (response.status === 401 && !isPublicAuthRequest(input, init)) {
    const refreshed = await refreshAuthSession();
    if (refreshed) response = await fetch(input, credentialed);
    if (response.status === 401) notifyAuthExpired();
  }
  return response;
}

export function resetAuthRefreshForTests(): void {
  refreshPromise = null;
  authChannel?.close();
  authChannel = null;
}
