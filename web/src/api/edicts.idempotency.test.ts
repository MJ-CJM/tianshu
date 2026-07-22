import { afterEach, describe, expect, it, vi } from "vitest";
import type { AxiosAdapter, AxiosRequestConfig } from "axios";
import apiClient from "./client";
import { createEdict } from "./edicts";
import { resetAuthRefreshForTests } from "./authFetch";

const originalAdapter = apiClient.defaults.adapter;

describe("Edict submission idempotency", () => {
  afterEach(() => {
    apiClient.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
    resetAuthRefreshForTests();
  });

  it("generates one key and reuses the same header and body across an auth retry", async () => {
    const randomUUID = vi.fn(() => "00000000-0000-4000-8000-000000000001");
    vi.stubGlobal("crypto", { randomUUID });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 200 })),
    );
    const requests: AxiosRequestConfig[] = [];
    const adapter: AxiosAdapter = async (config) => {
      requests.push(config);
      if (requests.length === 1) {
        return Promise.reject({
          config,
          message: "Unauthorized",
          response: { status: 401, data: {} },
        });
      }
      return {
        config,
        data: { success: true, data: { id: "edict-1" }, error: null, metadata: null },
        headers: {},
        status: 202,
        statusText: "Accepted",
      };
    };
    apiClient.defaults.adapter = adapter;

    await createEdict({ goal: "retry safely" });

    expect(randomUUID).toHaveBeenCalledTimes(1);
    expect(requests).toHaveLength(2);
    const bodies = requests.map((request) => JSON.parse(String(request.data)));
    const keys = requests.map((request) => request.headers?.["Idempotency-Key"]);
    expect(keys).toEqual([
      "00000000-0000-4000-8000-000000000001",
      "00000000-0000-4000-8000-000000000001",
    ]);
    expect(bodies.map((body) => body.idempotency_key)).toEqual(keys);
  });

  it("never sends browser-supplied actor fields", async () => {
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000002"),
    });
    let requestBody: Record<string, unknown> | null = null;
    apiClient.defaults.adapter = async (config) => {
      requestBody = JSON.parse(String(config.data)) as Record<string, unknown>;
      return {
        config,
        data: { success: true, data: { id: "edict-2" }, error: null, metadata: null },
        headers: {},
        status: 202,
        statusText: "Accepted",
      };
    };

    await createEdict({
      goal: "derive authority on the server",
      actor: "browser-forged-actor",
      submitter: "browser-forged-submitter",
    } as Parameters<typeof createEdict>[0] & { actor: string; submitter: string });

    expect(requestBody).not.toHaveProperty("actor");
    expect(requestBody).not.toHaveProperty("submitter");
    expect(requestBody).toMatchObject({
      goal: "derive authority on the server",
      idempotency_key: "00000000-0000-4000-8000-000000000002",
    });
  });
});
