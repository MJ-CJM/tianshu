// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import HealthDot from "./HealthDot";

const mockUseHealth = vi.fn();
vi.mock("../../hooks/useHealth", () => ({
  useHealth: () => mockUseHealth(),
}));
vi.mock("../../i18n", () => ({
  useT: () => (key: string) => key,
}));

describe("HealthDot readiness states", () => {
  afterEach(cleanup);

  it("renders ready state with ok label", () => {
    mockUseHealth.mockReturnValue({
      data: { schema_version: "1", status: "ready" },
      isError: false,
    });
    render(<HealthDot />);
    expect(screen.getByText("comp.healthDot.ok")).toBeDefined();
  });

  it("renders degraded state distinctly", () => {
    mockUseHealth.mockReturnValue({
      data: { schema_version: "1", status: "degraded" },
      isError: false,
    });
    render(<HealthDot />);
    expect(screen.getByText("comp.healthDot.degraded")).toBeDefined();
  });

  it("renders error label when not ready", () => {
    mockUseHealth.mockReturnValue({
      data: { schema_version: "1", status: "not_ready" },
      isError: false,
    });
    render(<HealthDot />);
    expect(screen.getByText("comp.healthDot.err")).toBeDefined();
  });

  it("renders error label on transport error", () => {
    mockUseHealth.mockReturnValue({ data: undefined, isError: true });
    render(<HealthDot />);
    expect(screen.getByText("comp.healthDot.err")).toBeDefined();
  });

  it("labels demo profile visibly when trusted-local detail reports it", () => {
    mockUseHealth.mockReturnValue({
      data: { schema_version: "1", status: "ready", profile: "demo" },
      isError: false,
    });
    render(<HealthDot />);
    expect(screen.getByText(/comp\.healthDot\.demo/)).toBeDefined();
  });
});
