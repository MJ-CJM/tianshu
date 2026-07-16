// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import EvolutionGate from "./EvolutionGate";

afterEach(cleanup);

describe("EvolutionGate", () => {
  it("reports not_enabled before S5 and cannot promote", async () => {
    const onPromote = vi.fn();
    render(
      <EvolutionGate
        status="not_enabled"
        view={{
          promotionAllowed: true,
          blockingGates: [],
          challengerRouting: { enabled: false, realTraffic: false, samples: null },
        }}
        onPromote={onPromote}
      />,
    );

    expect(screen.getByText("not_enabled")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "晋升" });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(onPromote).not.toHaveBeenCalled();
  });

  it("treats promotionAllowed as authoritative when the feature is enabled", () => {
    render(
      <EvolutionGate
        status="enabled"
        view={{
          promotionAllowed: false,
          blockingGates: [
            { code: "samples", label: "真实流量样本", current: 18, required: 50, evidenceUri: null },
          ],
          challengerRouting: { enabled: true, realTraffic: true, samples: 18 },
        }}
        onPromote={vi.fn()}
      />,
    );

    expect(screen.getByText("18 / 50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "晋升" })).toBeDisabled();
  });
});
