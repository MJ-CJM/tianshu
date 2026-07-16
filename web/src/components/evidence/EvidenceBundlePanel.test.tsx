// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import EvidenceBundlePanel from "./EvidenceBundlePanel";

afterEach(cleanup);

describe("EvidenceBundlePanel", () => {
  it("shows separated evidence, digest, download and governed replay warning", () => {
    render(
      <EvidenceBundlePanel
        bundle={{
          id: "evidence-1",
          digest: "sha256:abc123",
          downloadUrl: "/api/evidence/evidence-1/download",
          artifacts: ["report.json"],
          checks: ["unit tests: passed"],
          policies: ["network: allowlist"],
          cost: "¥1.25",
          environment: ["executor: codex"],
          auditorConclusion: "独立审计通过",
          missingMandatory: [],
          replayAvailable: true,
        }}
      />,
    );

    for (const heading of ["执行产物", "验收检查", "策略记录", "成本", "环境", "独立审计结论"]) {
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    }
    expect(screen.getByText("sha256:abc123")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载证据包" })).toHaveAttribute(
      "href",
      "/api/evidence/evidence-1/download",
    );
    expect(screen.getByRole("link", { name: "下载证据包" })).toHaveAttribute("download");
    expect(screen.getByText("重放会创建新的受治理敕令，不会在浏览器执行原命令。")).toBeInTheDocument();
  });
});
