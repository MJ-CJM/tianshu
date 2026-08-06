// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { WsMessage } from "../api/types";
import { useWsQueryInvalidation } from "./useWsQueryInvalidation";

type Listener = (message: WsMessage) => void;

function Harness({
  subscribe,
}: {
  subscribe: (listener: Listener) => () => void;
}) {
  useWsQueryInvalidation(subscribe);
  return null;
}

describe("Control Center WebSocket invalidation", () => {
  it("refreshes the Control Center for every consecutive governance event", () => {
    const listeners = new Set<Listener>();
    const subscribe = vi.fn((listener: Listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidate = vi
      .spyOn(client, "invalidateQueries")
      .mockResolvedValue();

    const view = render(
      <QueryClientProvider client={client}>
        <Harness subscribe={subscribe} />
      </QueryClientProvider>,
    );

    act(() => {
      for (const listener of listeners) {
        listener({ type: "audit.completed", edict_id: "edict-1" });
        listener({ type: "tool.approval_required", edict_id: "edict-1" });
        listener({
          type: "outer_loop.approval.requested",
          edict_id: "edict-1",
        });
      }
    });

    const controlInvalidations = invalidate.mock.calls.filter(
      ([filters]) =>
        JSON.stringify(filters?.queryKey) ===
        JSON.stringify(["control-center", "snapshot-v1"]),
    );
    expect(controlInvalidations).toHaveLength(3);

    view.unmount();
    expect(listeners).toHaveLength(0);
  });

  it("does not refresh the Control Center for unrelated consultation events", () => {
    const listeners = new Set<Listener>();
    const subscribe = (listener: Listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidate = vi
      .spyOn(client, "invalidateQueries")
      .mockResolvedValue();

    render(
      <QueryClientProvider client={client}>
        <Harness subscribe={subscribe} />
      </QueryClientProvider>,
    );
    act(() => {
      for (const listener of listeners) {
        listener({ type: "consultation.completed" });
      }
    });

    expect(invalidate).not.toHaveBeenCalledWith({
      queryKey: ["control-center", "snapshot-v1"],
    });
  });
});
