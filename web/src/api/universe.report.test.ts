import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());

vi.mock("./client", () => ({ default: { get, post } }));

import { generateTaiyiReport, getTaiyiReport } from "./universe";

describe("Taiyi report API", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it("uses GET only to read the latest report state", async () => {
    const state = { status: "not_generated", report: null, generated_at: null };
    get.mockResolvedValue({ data: { success: true, data: state } });

    await expect(getTaiyiReport()).resolves.toEqual({ success: true, data: state });
    expect(get).toHaveBeenCalledWith("/universes/taiyi/report");
    expect(post).not.toHaveBeenCalled();
  });

  it("uses POST for explicit report generation", async () => {
    const state = {
      status: "ready",
      generated_at: "2026-07-31T12:00:00Z",
      report: { count: 0, findings: [] },
    };
    post.mockResolvedValue({ data: { success: true, data: state } });

    await expect(generateTaiyiReport()).resolves.toEqual({ success: true, data: state });
    expect(post).toHaveBeenCalledWith("/universes/taiyi/report", {});
  });
});
