import { authFetch, notifyAuthExpired } from "./authFetch";

export type RuntimeMode = "trusted-local" | "secure-remote";
export type PrincipalKind = "local" | "human" | "service" | "webhook";

export interface Principal {
  id: string;
  kind: PrincipalKind;
  display_name: string;
  scopes: string[];
}

export interface AuthModeResponse {
  mode: RuntimeMode;
  login_required: boolean;
}

export interface AuthSessionResponse {
  principal: Principal;
  access_expires_at: string;
}

export interface AuthMeResponse {
  principal: Principal;
  source: "trusted-local" | "bearer" | "session-cookie" | "webhook";
  client_kind: "web" | "cli" | "mcp" | "api" | "webhook" | "system";
}

export class AuthApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "AuthApiError";
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as {
      detail?: string;
      error?: string | { message?: string };
    };
    if (payload.detail) return payload.detail;
    if (typeof payload.error === "string") return payload.error;
    if (payload.error?.message) return payload.error.message;
  } catch {
    // Authentication failures may have an empty response body.
  }
  return `HTTP ${response.status}`;
}

async function expectJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw new AuthApiError(response.status, await readError(response));
  return (await response.json()) as T;
}

export async function getAuthMode(): Promise<AuthModeResponse> {
  const response = await fetch("/api/auth/mode", { credentials: "include" });
  return expectJson<AuthModeResponse>(response);
}

export async function createAuthSession(token: string): Promise<AuthSessionResponse> {
  const response = await authFetch("/api/auth/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  return expectJson<AuthSessionResponse>(response);
}

export async function getAuthMe(): Promise<AuthMeResponse> {
  const response = await authFetch("/api/auth/me");
  return expectJson<AuthMeResponse>(response);
}

export async function deleteAuthSession(): Promise<void> {
  const response = await authFetch("/api/auth/session", { method: "DELETE" });
  if (!response.ok) throw new AuthApiError(response.status, await readError(response));
  notifyAuthExpired();
}
