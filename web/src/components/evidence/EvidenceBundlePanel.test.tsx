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
          status: "closed",
          version: 2,
          digest: "a".repeat(64),
          downloadUrl: "/api/evidence/evidence-1/download",
          executor: { id: "codex", displayName: "Codex executor", level: "managed" },
          artifacts: [{ digest: "b".repeat(64), mediaType: "application/json", sizeBytes: 128 }],
          checks: [{ name: "unit tests", status: "passed", exitCode: 0 }],
          policies: ["network: allowlist"],
          cost: "¥1.25",
          environment: ["executor: codex"],
          auditor: { id: "tianshu.independent.v1", verdict: "pass", reason: "独立审计通过" },
          missingMandatory: [],
          replayAvailable: true,
        }}
      />,
    );

    for (const heading of ["执行产物", "验收检查", "策略记录", "成本", "环境", "独立审计结论"]) {
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    }
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    expect(screen.getByText("版本").nextElementSibling).toHaveTextContent("2");
    expect(screen.getByRole("heading", { name: "执行器身份" })).toBeInTheDocument();
    expect(screen.getByText("Codex executor")).toBeInTheDocument();
    expect(screen.getByText("tianshu.independent.v1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载证据包" })).toHaveAttribute(
      "href",
      "/api/evidence/evidence-1/download",
    );
    expect(screen.getByRole("link", { name: "下载证据包" })).toHaveAttribute("download");
    expect(screen.getByText("重放会创建新的受治理敕令，不会在浏览器执行原命令。")).toBeInTheDocument();
  });

  it("shows open status and never exposes a download control before closure", () => {
    render(
      <EvidenceBundlePanel
        bundle={{
          id: "evidence-open",
          status: "open",
          version: 1,
          digest: null,
          downloadUrl: null,
          executor: { id: "claude-cli", displayName: "Claude CLI", level: "contained" },
          artifacts: [],
          checks: [{ name: "unit tests", status: "unavailable", exitCode: null }],
          policies: [],
          cost: "¥0.00",
          environment: [],
          auditor: {
            id: "tianshu.independent.v1",
            verdict: "fail",
            reason: "证据尚未闭合",
          },
          missingMandatory: ["check:unit tests"],
          replayAvailable: false,
        }}
      />,
    );

    expect(screen.getByText("生成中")).toBeInTheDocument();
    expect(screen.getByText("证据尚未闭合")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "下载证据包" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "受治理重放" })).not.toBeInTheDocument();
  });
});
