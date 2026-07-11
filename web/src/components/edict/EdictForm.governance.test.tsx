// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GovernanceCapabilityState, GovernanceContractPreview } from "../../api/types";

const edictsApi = vi.hoisted(() => ({
  parseEdict: vi.fn(),
  previewEdictGovernance: vi.fn(),
}));

vi.mock("../../api/edicts", () => edictsApi);
vi.mock("../../hooks/usePersonas", () => ({
  usePersonas: () => ({ data: [] }),
}));

import EdictForm from "./EdictForm";

const CAPABILITIES = [
  "action_interception",
  "workspace_control",
  "network_control",
  "secret_control",
  "budget_enforcement",
  "decision_bridge",
  "pause",
  "durable_resume",
  "event_fidelity",
  "artifact_export",
  "side_effect_receipts",
  "pre_run_restore_point",
  "governed_apply_merge",
] as const;

function advisoryPreview(): GovernanceContractPreview {
  const requestedContractHash = "a".repeat(64);
  const executor = {
    schema_version: "1",
    adapter_id: "native",
    model: null,
    config: [],
  };
  const objective = {
    schema_version: "1",
    goal: "谨慎执行",
    context: null,
    output_format: null,
    constraints: [],
  };
  const acceptance = {
    schema_version: "1",
    checks: [],
    critic_persona_ids: [],
    critic_model: null,
    critic_same_issue_threshold: 2,
    critic_strictness: "lenient",
    escalation_levels: ["L1", "L2", "L3"],
    l1_max_rounds: 2,
    l2_max_rounds: 1,
    l1_thinking_budget: 8000,
    l1_model_upgrade: null,
    l2_consultation_personas: [],
    min_outer_iterations: 1,
    max_outer_iterations: 5,
    deadline_seconds: null,
    on_exhaustion: "escalate",
    on_critic_unavailable: "skip",
    on_approval_timeout: "best_effort",
  };
  const permissions = {
    schema_version: "1",
    approval_required_tools: [],
    allowed_paths: [],
    allowed_bash_prefixes: [],
    tier_overrides: [],
    auto_approve_max_tier: 1,
    expires_after_seconds: null,
    policy_profile_name: null,
    secret_refs: [],
    review_policy: "always",
  };
  const network = {
    schema_version: "1",
    mode: "deny",
    allowed_hosts: [],
    write_hosts: [],
    methods: ["GET", "HEAD"],
  };
  const workspace = {
    schema_version: "1",
    source_id: null,
    base_revision: null,
    staging_mode: "legacy_shared",
    apply_mode: "none",
    require_clean_source: false,
  };
  const budget = {
    schema_version: "1",
    token_limit: null,
    cost_limit_cny: null,
    wall_clock_seconds: 300,
    max_iterations: 20,
    max_concurrency: 1,
    retry_limit: 0,
  };
  const recovery = {
    schema_version: "1",
    require_restore_point: false,
    failure_cleanup: "best_effort",
    rollback_on_apply_failure: true,
  };
  const capabilities = {
    schema_version: "1",
    mandatory: [],
    advisory: ["durable_resume"],
  };
  const requestedContract = {
    schema_version: "1",
    objective,
    acceptance,
    executor,
    capabilities,
    permissions,
    network,
    workspace,
    budget,
    recovery,
  };
  const stateAt = (index: number): GovernanceCapabilityState => {
    if (index === 0) return "enforced";
    if (index === 1) return "best_effort";
    if (index === 2) return "observed";
    return "unsupported";
  };

  return {
    compatible: true,
    requested_contract: requestedContract,
    requested_contract_hash: requestedContractHash,
    effective_contract: {
      schema_version: "1",
      requested_contract_hash: requestedContractHash,
      objective,
      acceptance,
      executor,
      permissions,
      network,
      workspace,
      budget,
      recovery,
      executor_manifest_id: "tianshu.native.v1",
      executor_manifest_version: "1",
      executor_manifest_hash: "b".repeat(64),
      runtime_probe_id: "host-test",
      effective_controls: CAPABILITIES.map((capability, index) => ({
        schema_version: "1",
        capability,
        requested_mode: capability === "durable_resume" ? "advisory" : "unrequested",
        state: stateAt(index),
        evidence: [],
      })),
      unsupported_advisory: ["durable_resume"],
      degradations: [],
      resolved_source_id: null,
      resolved_base_revision: null,
    },
    mandatory_mismatches: [],
    execution_mode: "single",
    execution_mode_mismatches: [],
    advisory_gaps: ["durable_resume"],
    executor_level: "contained",
    experimental: false,
    manifest_hash: "b".repeat(64),
    runtime_probe_id: "host-test",
  };
}

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  edictsApi.previewEdictGovernance.mockReset();
  edictsApi.previewEdictGovernance.mockResolvedValue(advisoryPreview());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("edict governance confirmation", () => {
  it("shows the effective contract and waits for explicit consent before dispatching advisory gaps", async () => {
    const onSubmit = vi.fn();
    render(<EdictForm onSubmit={onSubmit} loading={false} />);

    fireEvent.change(screen.getByLabelText("敕令旨意"), {
      target: { value: "谨慎执行" },
    });
    fireEvent.click(screen.getByRole("button", { name: /颁发敕令/ }));

    expect(await screen.findByText(/durable_resume/)).not.toBeNull();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText("生效契约摘要")).not.toBeNull();
    expect(screen.getByText(/tianshu\.native\.v1/)).not.toBeNull();
    expect(screen.getByText(/强制 1 · 尽力 1 · 观测 1 · 不支持 10/)).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "知悉风险，继续下发" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(edictsApi.previewEdictGovernance).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        goal: "谨慎执行",
        governance_contract: advisoryPreview().requested_contract,
      }),
    );
  });
});
