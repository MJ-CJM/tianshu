import { beforeEach, describe, expect, it, vi } from "vitest";

const query = vi.hoisted(() => ({ useQuery: vi.fn() }));

vi.mock("@tanstack/react-query", () => ({ useQuery: query.useQuery }));

import { useControlCenter } from "./useControlCenter";

describe("useControlCenter", () => {
  beforeEach(() => {
    query.useQuery.mockReset();
    query.useQuery.mockReturnValue({
      data: null,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("polls every five seconds only while the page is visible", () => {
    useControlCenter();

    expect(query.useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        refetchInterval: 5_000,
        refetchIntervalInBackground: false,
      }),
    );
  });
});
