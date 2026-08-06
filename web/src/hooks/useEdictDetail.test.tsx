// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EdictDetailSnapshotV1 } from "../api/edicts";

const api = vi.hoisted(() => ({
  getDetail: vi.fn(),
  getEvents: vi.fn(),
  resolve: vi.fn(),
  replay: vi.fn(),
}));

vi.mock("../api/edicts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/edicts")>()),
  getEdictDetailSnapshot: api.getDetail,
  getEdictEvents: api.getEvents,
  resolveEdictDecision: api.resolve,
  replayGovernedEdict: api.replay,
}));

import { EDICT_DETAIL_QUERY_KEY, useEdictDetail } from "./useEdictDetail";

const SNAPSHOT = {
  schema_version: 1,
  edict: { id: "edict-1", status: "open" },
  memorials: [],
  runs: [],
  decisions: [],
  evidence: [],
} as unknown as EdictDetailSnapshotV1;

function setup(client?: QueryClient) {
  const queryClient =
    client ??
    new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return {
    queryClient,
    ...renderHook(() => useEdictDetail("edict-1"), { wrapper }),
  };
}

beforeEach(() => {
  api.getDetail.mockReset();
  api.getEvents.mockReset();
  api.resolve.mockReset();
  api.replay.mockReset();
  api.getDetail.mockResolvedValue(SNAPSHOT);
  api.getEvents.mockResolvedValue({ success: true, data: [] });
});

describe("useEdictDetail", () => {
  it("loads the composed durable snapshot and preserves the legacy page shape", async () => {
    const { result } = setup();

    await waitFor(() => expect(result.current.status).toBe("success-empty"));
    expect(result.current.edict?.id).toBe("edict-1");
    expect(result.current.detail).toBe(SNAPSHOT);
    expect(result.current.memorials).toEqual([]);
    expect(result.current.events).toEqual([]);
    expect(api.getDetail).toHaveBeenCalledWith("edict-1");
  });

  it("keeps cached authority visible when refresh becomes stale", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    client.setQueryData(EDICT_DETAIL_QUERY_KEY("edict-1"), SNAPSHOT);
    api.getDetail.mockRejectedValue({
      status: 500,
      code: "detail-refresh-failed",
      message: "刷新敕令详情失败",
      correlationId: "corr-detail",
      retryable: true,
    });

    const { result } = setup(client);
    await waitFor(() => expect(result.current.status).toBe("stale"));
    expect(result.current.edict?.id).toBe("edict-1");
    expect(result.current.problem?.message).toBe("刷新敕令详情失败");
  });

  it("does not let the legacy event feed redefine composed governance truth", async () => {
    api.getEvents.mockRejectedValue(new Error("legacy events unavailable"));
    const { result } = setup();

    await waitFor(() => expect(result.current.status).toBe("success-empty"));
    expect(result.current.detail).toBe(SNAPSHOT);
    expect(result.current.problem).toBeNull();
  });

  it("invalidates every authoritative consumer after decision and governed replay mutations", async () => {
    api.resolve.mockResolvedValue({ status: "resolved", version: 2 });
    api.replay.mockResolvedValue("edict-replay");
    const { result, queryClient } = setup();
    const invalidate = vi
      .spyOn(queryClient, "invalidateQueries")
      .mockResolvedValue();
    await waitFor(() => expect(result.current.status).toBe("success-empty"));

    await act(async () => {
      await result.current.resolveDecision({
        decisionRequestId: "decision-1",
        kind: "plan_review",
        action: "approve",
        reason: "方案已核验",
        expectedVersion: 1,
      });
    });
    await act(async () => {
      await result.current.replay({
        title: "Replay",
        goal: "Replay safely",
        context: null,
        priority: "normal",
        governanceContract: { schema_version: "1" },
      });
    });

    for (const queryKey of [
      EDICT_DETAIL_QUERY_KEY("edict-1"),
      ["decisions", "edict-1"],
      ["run-state", "edict-1"],
      ["evidence", "edict-1"],
      ["control-center", "snapshot-v1"],
      ["edicts"],
    ]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey });
    }
  });
});
