// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import EvolutionGate from "./EvolutionGate";

afterEach(cleanup);

describe("EvolutionGate", () => {
  it("renders authoritative blocking evidence and routing without a mutation button", async () => {
    const user = userEvent.setup();
    render(
      <EvolutionGate
        candidate={{
          candidate_id: "candidate-skill-7",
          kind: "skill",
          version: 7,
          lifecycle: "canary",
          artifact_hash: "a".repeat(64),
          promotion_allowed: false,
          rollback_state: "ready",
          gates: [{
            code: "minimum_samples",
            status: "failed",
            blocking: true,
            current: 18,
            required: 50,
            evidence_bundle_id: "evidence:gate-samples",
            evidence_hash: "b".repeat(64),
          }],
        }}
        routing={{
          candidate_id: "candidate-skill-7",
          routing_version: 3,
          allocation_percent: 10,
          champion_assignment_count: 82,
          challenger_assignment_count: 18,
        }}
      />,
    );

    expect(screen.getByText("18 / 50")).toBeInTheDocument();
    expect(screen.getByText("b".repeat(64))).toBeInTheDocument();
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.getAllByText("18")).toHaveLength(1);
    const evidence = screen.getByRole("link", { name: "查看门禁证据" });
    expect(evidence).toHaveAttribute(
      "href",
      "/api/evidence/evidence%3Agate-samples/download",
    );
    await user.tab();
    expect(document.activeElement).toBe(evidence);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
