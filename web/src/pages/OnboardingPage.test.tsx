// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GovernanceContractPreview, PersonaInfo, SkillInfo } from "../api/types";
import type { OnboardingState } from "../api/onboarding";

const onboardingApi = vi.hoisted(() => ({ getOnboardingState: vi.fn() }));
const edictsApi = vi.hoisted(() => ({
  createEdict: vi.fn(),
  parseEdict: vi.fn(),
  previewEdictGovernance: vi.fn(),
}));

vi.mock("../api/onboarding", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/onboarding")>()),
  getOnboardingState: onboardingApi.getOnboardingState,
}));
vi.mock("../api/edicts", () => edictsApi);
vi.mock("../hooks/usePersonas", () => ({ usePersonas: () => ({ data: [] }) }));

import OnboardingPage from "./OnboardingPage";

const PERSONAS: PersonaInfo[] = [
  ["bingbu", "兵部"],
  ["ducha", "都察院"],
  ["hubu", "户部"],
  ["neige", "内阁"],
  ["tongzheng", "通政司"],
  ["wenyuan", "文渊阁"],
].map(([id, name]) => ({
  id: id!,
  name: name!,
  department: id!,
  tools_allowed: [],
  tools_denied: [],
  skills_allowed: [],
  tool_tier_max: 1,
  can_delegate: false,
  memory_global_read: false,
  delegates_to: [],
}));

const SKILLS: SkillInfo[] = ["file-ops", "shell"].map((name) => ({
  name,
  description: `${name} builtin`,
  source: "builtin",
  always: false,
  tool_tier: null,
  path: `/builtin/${name}`,
  content_length: 1,
}));

const FRESH_STATE: OnboardingState = {
  required: true,
  readiness: "ready",
  profile: "demo",
  packagedPersonas: PERSONAS,
  builtinSkills: SKILLS,
};

function governancePreview(): GovernanceContractPreview {
  const requestedContract = {
    executor: { adapter_id: "native" },
    capabilities: { mandatory: ["workspace_control"], advisory: [] },
    permissions: { review_policy: "always", allowed_paths: ["workspace"] },
    network: { mode: "deny", allowed_hosts: [] },
    workspace: { source_id: "workspace-main", base_revision: "HEAD" },
    budget: { token_limit: 2000, wall_clock_seconds: 300 },
    acceptance: { checks: ["tests"], deadline_seconds: 600, on_exhaustion: "escalate" },
    recovery: { require_restore_point: true, failure_cleanup: "strict" },
  };
  return {
    compatible: true,
    requested_contract: requestedContract,
    requested_contract_hash: "a".repeat(64),
    effective_contract: {
      requested_contract_hash: "a".repeat(64),
      executor: { adapter_id: "native" },
      executor_manifest_id: "tianshu.native.v1",
      executor_manifest_version: "1",
      runtime_probe_id: "host-test",
      effective_controls: [],
      unsupported_advisory: [],
    },
    mandatory_mismatches: [],
    execution_mode: "single",
    execution_mode_mismatches: [],
    advisory_gaps: [],
    executor_level: "contained",
    experimental: false,
    manifest_hash: "b".repeat(64),
    runtime_probe_id: "host-test",
  };
}

function LocationProbe() {
  return <output>{useLocation().pathname}</output>;
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/onboarding"]}>
        <LocationProbe />
        <Routes>
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route path="/control" element={<h1>中枢总览</h1>} />
          <Route path="/edicts/:edictId" element={<h1>敕令详情</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
  onboardingApi.getOnboardingState.mockReset();
  edictsApi.createEdict.mockReset();
  edictsApi.previewEdictGovernance.mockReset();
  edictsApi.previewEdictGovernance.mockResolvedValue(governancePreview());
  edictsApi.createEdict.mockResolvedValue({
    success: true,
    data: { id: "edict-new" },
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("first-run onboarding", () => {
  it("keeps unresolved readiness in loading instead of inferring a fresh install", () => {
    onboardingApi.getOnboardingState.mockReturnValue(new Promise(() => {}));
    renderPage();

    expect(screen.getByText("正在加载").closest("section")).toHaveAttribute("role", "status");
    expect(screen.queryByText("兵部")).not.toBeInTheDocument();
  });

  it.each([401, 403])("maps HTTP %s to permission denied", async (status) => {
    onboardingApi.getOnboardingState.mockRejectedValue({
      status,
      code: "permission-denied",
      message: "",
      correlationId: null,
      retryable: false,
    });
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(/无权/);
  });

  it("maps Doctor/readiness failure to service unavailable", async () => {
    onboardingApi.getOnboardingState.mockRejectedValue({
      status: 503,
      code: "onboarding-readiness-unavailable",
      message: "",
      correlationId: null,
      retryable: true,
    });
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.queryByText("兵部")).not.toBeInTheDocument();
  });

  it("redirects a truthfully configured installation to control", async () => {
    onboardingApi.getOnboardingState.mockResolvedValue({ ...FRESH_STATE, required: false });
    renderPage();

    expect(await screen.findByText("/control")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "中枢总览" })).toBeInTheDocument();
  });

  it("shows exactly the packaged resources and requires an explicit demo acknowledgement by keyboard", async () => {
    onboardingApi.getOnboardingState.mockResolvedValue(FRESH_STATE);
    renderPage();

    const departments = await screen.findByRole("list", { name: "预置部门" });
    expect(within(departments).getAllByRole("listitem")).toHaveLength(6);
    for (const persona of PERSONAS) {
      expect(within(departments).getByText(persona.name)).toBeInTheDocument();
    }
    const skills = screen.getByRole("list", { name: "内置技能" });
    expect(within(skills).getAllByRole("listitem")).toHaveLength(2);
    expect(within(skills).getByText("file-ops")).toBeInTheDocument();
    expect(within(skills).getByText("shell")).toBeInTheDocument();

    const profile = screen.getByRole("radio", { name: /演示配置/ });
    expect(profile).not.toBeChecked();
    expect(screen.getByText(/确定性模拟提供商/)).toBeInTheDocument();
    expect(screen.queryByLabelText("敕令旨意")).not.toBeInTheDocument();

    await userEvent.tab();
    while (document.activeElement !== profile) await userEvent.tab();
    await userEvent.keyboard("[Space]");

    expect(profile).toBeChecked();
    expect(await screen.findByLabelText("敕令旨意")).toBeInTheDocument();
  });

  it("previews requested/effective truth, omits browser actors, and opens the real Edict", async () => {
    onboardingApi.getOnboardingState.mockResolvedValue(FRESH_STATE);
    renderPage();
    await userEvent.click(await screen.findByRole("radio", { name: /演示配置/ }));
    fireEvent.change(await screen.findByLabelText("敕令旨意"), {
      target: { value: "完成首次治理任务" },
    });
    await userEvent.click(screen.getByRole("button", { name: /颁发敕令/ }));

    const requested = await screen.findByRole("region", { name: "请求契约" });
    const effective = screen.getByRole("region", { name: "生效契约" });
    expect(requested).toHaveTextContent("workspace-main");
    expect(requested).toHaveTextContent("deny");
    expect(requested).toHaveTextContent("workspace_control");
    expect(requested).toHaveTextContent("workspace");
    expect(requested).toHaveTextContent("2000");
    expect(requested).toHaveTextContent("600");
    expect(requested).toHaveTextContent("tests");
    expect(requested).toHaveTextContent("strict");
    expect(effective).toHaveTextContent("tianshu.native.v1");
    expect(edictsApi.createEdict).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "确认契约并下发" }));

    await waitFor(() => expect(edictsApi.createEdict).toHaveBeenCalledOnce());
    const submitted = edictsApi.createEdict.mock.calls[0]![0];
    expect(submitted).not.toHaveProperty("actor");
    expect(submitted).not.toHaveProperty("submitter");
    expect(submitted).toHaveProperty("governance_contract", governancePreview().requested_contract);
    expect(await screen.findByText("/edicts/edict-new")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "敕令详情" })).toBeInTheDocument();
  });
});
