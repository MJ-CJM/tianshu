import axios from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("antd", () => ({ notification: { error: vi.fn() } }));
vi.mock("./authFetch", () => ({
  notifyAuthExpired: vi.fn(),
  refreshAuthSession: vi.fn(async () => false),
}));

import apiClient, { toApiProblem } from "./client";
import { getDagByEdict } from "./dag";
import { getSupervisionReports } from "./edicts";

const originalAdapter = apiClient.defaults.adapter;

function httpError(status: number, data: unknown = {}, headers: unknown = {}) {
  return {
    isAxiosError: true,
    message: `HTTP ${status}`,
    config: { headers: {} },
    response: { status, data, headers },
  };
}

afterEach(() => {
  apiClient.defaults.adapter = originalAdapter;
  vi.clearAllMocks();
});

describe("ApiProblem client boundary", () => {
  it.each([
    [401, "auth-required", false],
    [403, "permission-denied", false],
    [503, "service-unavailable", true],
  ])(
    "maps HTTP %s without fabricating an empty success",
    (status, code, retryable) => {
      const problem = toApiProblem(
        httpError(
          status,
          { detail: "truthful failure" },
          { "x-correlation-id": `corr-${status}` },
        ),
      );

      expect(problem).toEqual({
        status,
        code,
        message: "truthful failure",
        correlationId: `corr-${status}`,
        retryable,
      });
    },
  );

  it("reads correlation ids from real AxiosHeaders", () => {
    const headers = new axios.AxiosHeaders({
      "X-Correlation-ID": "corr-axios",
    });

    expect(toApiProblem(httpError(503, {}, headers)).correlationId).toBe(
      "corr-axios",
    );
  });

  it("rejects structured success:false bodies through the same mapping", async () => {
    apiClient.defaults.adapter = vi.fn(async (config) => ({
      config,
      data: {
        success: false,
        error: "contract rejected",
        code: "contract-rejected",
      },
      headers: { "x-correlation-id": "corr-body" },
      status: 200,
      statusText: "OK",
    }));

    await expect(apiClient.get("/contract")).rejects.toMatchObject({
      status: 200,
      code: "contract-rejected",
      message: "contract rejected",
      correlationId: "corr-body",
      retryable: false,
    });
  });

  it("rejects detail-only success:false bodies instead of treating them as success", async () => {
    apiClient.defaults.adapter = vi.fn(async (config) => ({
      config,
      data: {
        success: false,
        detail: {
          code: "governance-blocked",
          message: "mandatory control missing",
        },
      },
      headers: {},
      status: 200,
      statusText: "OK",
    }));

    await expect(apiClient.get("/governance")).rejects.toMatchObject({
      status: 200,
      code: "governance-blocked",
      message: "mandatory control missing",
    });
  });

  it("preserves 503 from a page wrapper instead of returning an empty array", async () => {
    apiClient.defaults.adapter = vi.fn(async () => {
      throw httpError(503, { detail: "maintenance" });
    });

    await expect(getSupervisionReports("edict-1")).rejects.toMatchObject({
      status: 503,
      code: "service-unavailable",
    });
  });

  it("keeps an explicitly silent 404 as the DAG absence state", async () => {
    apiClient.defaults.adapter = vi.fn(async () => {
      throw httpError(404, { detail: "not found" });
    });

    await expect(getDagByEdict("edict-without-dag")).resolves.toBeNull();
  });

  it("keeps Axios detection confined to the client boundary", () => {
    expect(axios.isAxiosError(httpError(503))).toBe(true);
  });
});
