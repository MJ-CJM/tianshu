import axios from "axios";
import { notification } from "antd";
import { notifyAuthExpired, refreshAuthSession } from "./authFetch";
import type { ApiProblem } from "../contracts/api";

type ErrorBody = {
  code?: unknown;
  detail?: unknown;
  error?: unknown;
  message?: unknown;
  correlation_id?: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function defaultProblemCode(status: number): string {
  if (status === 401) return "auth-required";
  if (status === 403) return "permission-denied";
  if (status === 404) return "not-found";
  if (status === 409 || status === 412) return "stale";
  if (status === 503) return "service-unavailable";
  if (status === 0) return "network-error";
  return "request-failed";
}

function isRetryable(status: number): boolean {
  return status === 0 || status === 408 || status === 429 || status >= 500;
}

function headerValue(headers: unknown, name: string): string | null {
  if (!isRecord(headers)) return null;
  const getter = headers.get;
  if (typeof getter === "function") {
    const value = getter.call(headers, name);
    if (typeof value === "string" && value) return value;
  }
  const entry = Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase());
  const value = entry?.[1];
  return typeof value === "string" && value ? value : null;
}

function problemFromParts(
  status: number,
  bodyValue: unknown,
  headers: unknown,
  fallbackMessage: string,
): ApiProblem {
  const body: ErrorBody = isRecord(bodyValue) ? bodyValue : {};
  const detail = isRecord(body.detail) ? body.detail : null;
  const codeValue = body.code ?? detail?.code;
  const messageValue =
    (typeof body.detail === "string" ? body.detail : undefined) ??
    detail?.message ??
    body.error ??
    body.message;
  const correlationValue = body.correlation_id ?? detail?.correlation_id;

  return {
    status,
    code: typeof codeValue === "string" && codeValue ? codeValue : defaultProblemCode(status),
    message:
      typeof messageValue === "string" && messageValue ? messageValue : fallbackMessage,
    correlationId:
      headerValue(headers, "x-correlation-id") ??
      (typeof correlationValue === "string" && correlationValue ? correlationValue : null),
    retryable: isRetryable(status),
  };
}

export function isApiProblem(value: unknown): value is ApiProblem {
  return (
    isRecord(value) &&
    typeof value.status === "number" &&
    typeof value.code === "string" &&
    typeof value.message === "string" &&
    (typeof value.correlationId === "string" || value.correlationId === null) &&
    typeof value.retryable === "boolean"
  );
}

export function toApiProblem(error: unknown): ApiProblem {
  if (isApiProblem(error)) return error;
  if (axios.isAxiosError(error)) {
    return problemFromParts(
      error.response?.status ?? 0,
      error.response?.data,
      error.response?.headers,
      error.message || "网络错误",
    );
  }
  return problemFromParts(
    0,
    null,
    null,
    error instanceof Error ? error.message : "网络错误",
  );
}

const apiClient = axios.create({
  baseURL: "/api",
  timeout: 30_000,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

apiClient.interceptors.response.use(
  (response) => {
    const body = response.data;
    if (body && body.success === false) {
      const problem = problemFromParts(
        response.status,
        body,
        response.headers,
        "请求失败",
      );
      notification.error({
        message: "请求失败",
        description: problem.message,
        placement: "topRight",
      });
      return Promise.reject(problem);
    }
    return response;
  },
  async (error) => {
    const original = error.config as (typeof error.config & { _authRetried?: boolean }) | undefined;
    const url = String(original?.url ?? "");
    const isProtectedUnauthorized =
      error.response?.status === 401 &&
      !url.includes("/auth/session") &&
      !url.includes("/auth/refresh");
    if (
      isProtectedUnauthorized &&
      original &&
      !original._authRetried
    ) {
      original._authRetried = true;
      if (await refreshAuthSession()) return apiClient.request(original);
    }
    if (isProtectedUnauthorized) notifyAuthExpired();
    const problem = toApiProblem(error);
    // 允许请求通过 config.silentCodes 标记可静默的 HTTP 状态码
    const silentCodes: number[] = error.config?.silentCodes ?? [];
    if (silentCodes.includes(problem.status)) {
      return Promise.reject(problem);
    }
    notification.error({
      message: "请求异常",
      description: problem.message,
      placement: "topRight",
    });
    return Promise.reject(problem);
  },
);

export default apiClient;
