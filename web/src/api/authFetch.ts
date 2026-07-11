let refreshPromise: Promise<boolean> | null = null;

export function refreshAuthSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "include",
    })
      .then((response) => response.ok)
      .catch(() => false)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

function isRefreshRequest(input: RequestInfo | URL): boolean {
  const url = typeof input === "string" ? input : input.toString();
  return url.includes("/api/auth/refresh") || url.includes("/api/auth/session");
}

export async function authFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const credentialed = { ...init, credentials: "include" as const };
  let response = await fetch(input, credentialed);
  if (response.status === 401 && !isRefreshRequest(input)) {
    const refreshed = await refreshAuthSession();
    if (refreshed) response = await fetch(input, credentialed);
  }
  return response;
}

export function resetAuthRefreshForTests(): void {
  refreshPromise = null;
}
