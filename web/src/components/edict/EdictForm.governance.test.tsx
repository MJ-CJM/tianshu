// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

    expect(await screen.findAllByText(/durable_resume/)).not.toHaveLength(0);
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

  it("keeps requested and effective contract truth separate before every confirmed dispatch", async () => {
    const preview = advisoryPreview();
    preview.advisory_gaps = [];
    preview.requested_contract = {
      ...preview.requested_contract,
      permissions: {
        ...(preview.requested_contract.permissions as Record<string, unknown>),
        allowed_paths: ["requested/path"],
        review_policy: "always",
      },
      network: {
        ...(preview.requested_contract.network as Record<string, unknown>),
        mode: "allowlist",
        allowed_hosts: ["requested.example"],
      },
      workspace: {
        ...(preview.requested_contract.workspace as Record<string, unknown>),
        source_id: "workspace-requested",
        base_revision: "requested-rev",
      },
      budget: {
        ...(preview.requested_contract.budget as Record<string, unknown>),
        token_limit: 2000,
        wall_clock_seconds: 300,
      },
      acceptance: {
        ...(preview.requested_contract.acceptance as Record<string, unknown>),
        checks: [{ name: "requested-check" }],
        deadline_seconds: 600,
      },
      recovery: {
        ...(preview.requested_contract.recovery as Record<string, unknown>),
        require_restore_point: true,
        failure_cleanup: "required",
      },
    };
    preview.effective_contract = {
      ...preview.effective_contract!,
      permissions: {
        ...(preview.effective_contract!.permissions as Record<string, unknown>),
        allowed_paths: ["effective/path"],
        review_policy: "on_failure",
      },
      network: {
        ...(preview.effective_contract!.network as Record<string, unknown>),
        mode: "deny",
        allowed_hosts: [],
      },
      workspace: {
        ...(preview.effective_contract!.workspace as Record<string, unknown>),
        source_id: "workspace-policy",
        base_revision: "policy-rev",
      },
      resolved_source_id: "workspace-effective",
      resolved_base_revision: "effective-rev",
      budget: {
        ...(preview.effective_contract!.budget as Record<string, unknown>),
        token_limit: 1000,
        wall_clock_seconds: 120,
      },
      acceptance: {
        ...(preview.effective_contract!.acceptance as Record<string, unknown>),
        checks: [{ name: "effective-check" }],
        deadline_seconds: 300,
      },
      recovery: {
        ...(preview.effective_contract!.recovery as Record<string, unknown>),
        require_restore_point: false,
        failure_cleanup: "best_effort",
      },
      unsupported_advisory: [],
    };
    edictsApi.previewEdictGovernance.mockResolvedValue(preview);
    const onSubmit = vi.fn();
    render(
      <EdictForm
        onSubmit={onSubmit}
        loading={false}
        governanceConfirmation="always"
      />,
    );

    fireEvent.change(screen.getByLabelText("敕令旨意"), {
      target: { value: "谨慎执行" },
    });
    fireEvent.click(screen.getByRole("button", { name: /颁发敕令/ }));

    const requested = await screen.findByRole("region", { name: "请求契约" });
    expect(within(requested).getByText("native")).not.toBeNull();
    expect(within(requested).getByText("workspace-requested")).not.toBeNull();
    expect(within(requested).getByText("requested-rev")).not.toBeNull();
    expect(within(requested).getByText("allowlist")).not.toBeNull();
    expect(requested).toHaveTextContent("requested/path");
    expect(requested).toHaveTextContent("requested.example");
    expect(requested).toHaveTextContent("2000");
    expect(requested).toHaveTextContent("requested-check");
    expect(requested).toHaveTextContent("600");
    expect(requested).toHaveTextContent("required");

    const effective = screen.getByRole("region", { name: "生效契约" });
    expect(within(effective).getByText("tianshu.native.v1")).not.toBeNull();
    expect(within(effective).getByText("contained")).not.toBeNull();
    expect(effective).toHaveTextContent("workspace-effective");
    expect(effective).toHaveTextContent("effective-rev");
    expect(effective).toHaveTextContent("effective/path");
    expect(effective).toHaveTextContent("on_failure");
    expect(effective).toHaveTextContent("deny");
    expect(effective).toHaveTextContent("1000");
    expect(effective).toHaveTextContent("effective-check");
    expect(effective).toHaveTextContent("300");
    expect(effective).toHaveTextContent("best_effort");
    expect(effective).toHaveTextContent(/workspace_control.*best_effort/);
    expect(effective).toHaveTextContent(/network_control.*observed/);
    expect(effective).toHaveTextContent(/budget_enforcement.*unsupported/);
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认契约并下发" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ governance_contract: preview.requested_contract }),
    );
  });

  it("fails closed when a compatible preview omits the effective contract", async () => {
    const preview = advisoryPreview();
    preview.compatible = true;
    preview.effective_contract = null;
    preview.advisory_gaps = [];
    edictsApi.previewEdictGovernance.mockResolvedValue(preview);
    const onSubmit = vi.fn();
    render(
      <EdictForm
        onSubmit={onSubmit}
        loading={false}
        governanceConfirmation="always"
      />,
    );

    fireEvent.change(screen.getByLabelText("敕令旨意"), {
      target: { value: "拒绝不完整预检" },
    });
    fireEvent.click(screen.getByRole("button", { name: /颁发敕令/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("生效契约");
    expect(screen.queryByRole("button", { name: "确认契约并下发" })).toBeNull();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
