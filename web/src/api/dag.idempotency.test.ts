import type { AxiosAdapter, AxiosRequestConfig } from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";

import apiClient from "./client";
import { retryDag } from "./dag";

const originalAdapter = apiClient.defaults.adapter;

describe("DAG retry idempotency", () => {
  afterEach(() => {
    apiClient.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
  });

  it("sends the Idempotency-Key required by the retry endpoint", async () => {
    const randomUUID = vi.fn(() => "00000000-0000-4000-8000-0000000000da");
    vi.stubGlobal("crypto", { randomUUID });
    const requests: AxiosRequestConfig[] = [];
    const adapter: AxiosAdapter = async (config) => {
      requests.push(config);
      return {
        config,
        data: {
          success: true,
          data: { dag_id: "dag-1", reset_node_ids: ["node-1"] },
        },
        headers: {},
        status: 200,
        statusText: "OK",
      };
    };
    apiClient.defaults.adapter = adapter;

    await retryDag("dag-1", ["node-1"]);

    expect(randomUUID).toHaveBeenCalledTimes(1);
    expect(requests).toHaveLength(1);
    expect(requests[0]!.url).toContain("/dag/dag-1/retry");
    expect(requests[0]!.headers?.["Idempotency-Key"]).toBe(
      "00000000-0000-4000-8000-0000000000da",
    );
  });
});
