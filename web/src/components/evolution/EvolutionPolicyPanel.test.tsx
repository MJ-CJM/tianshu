// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SkillInfo } from "../../api/types";

const mocks = vi.hoisted(() => ({
  admin: true,
  savePolicy: vi.fn(),
  queryProblem: null as null | {
    status: number;
    code: string;
    message: string;
    correlationId: string | null;
    retryable: boolean;
  },
  saveProblem: null as null | {
    status: number;
    code: string;
    message: string;
    correlationId: string | null;
    retryable: boolean;
  },
  policies: [] as Array<{
    subject_key: string;
    kind: "skill";
    mode: "frozen" | "manual" | "canary";
    max_canary_basis_points: number;
    version: number;
    updated_at: string;
  }>,
  skills: [] as SkillInfo[],
  enabledValues: [] as boolean[],
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    principal: {
      id: "user:test",
      kind: "human",
      display_name: "Test",
      scopes: mocks.admin ? ["api", "admin"] : ["api"],
    },
  }),
}));
vi.mock("../../hooks/useSystem", () => ({
  useSkills: () => ({
    data: mocks.skills,
    isPending: false,
    error: null,
  }),
}));
vi.mock("../../hooks/useEvolutionPolicies", () => ({
  useEvolutionPolicies: (enabled: boolean) => {
    mocks.enabledValues.push(enabled);
    return {
      policies: mocks.policies,
      isLoading: false,
      problem: mocks.queryProblem,
      savePolicy: mocks.savePolicy,
      savingSubjectKey: null,
      saveProblem: mocks.saveProblem,
    };
  },
}));

import EvolutionPolicyPanel from "./EvolutionPolicyPanel";

beforeEach(() => {
  mocks.admin = true;
  mocks.policies = [];
  mocks.queryProblem = null;
  mocks.saveProblem = null;
  mocks.skills = [{
    name: "reviewer",
    description: "Review changes",
    source: "workspace",
    always: false,
    tool_tier: null,
    path: "/workspace/reviewer/SKILL.md",
    content_length: 128,
    pinned: true,
  }];
  mocks.enabledValues = [];
  mocks.savePolicy.mockReset();
  mocks.savePolicy.mockResolvedValue({
    subject_key: "skill:reviewer",
    kind: "skill",
    mode: "frozen",
    max_canary_basis_points: 1_000,
    version: 1,
    updated_at: "2026-08-26T00:00:00+00:00",
  });
});
afterEach(cleanup);

describe("EvolutionPolicyPanel", () => {
  it("keeps availability and curator protection truthful while creating a missing CAS row", async () => {
    const user = userEvent.setup();
    render(<EvolutionPolicyPanel />);

    expect(screen.getByText("Skill Loader 今可取用")).toBeInTheDocument();
    expect(screen.getByText("策展护持已立")).toBeInTheDocument();
    expect(screen.getByText("承默认之则：灰度")).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox"), "frozen");
    await user.click(screen.getByRole("button", { name: "存此策略" }));

    await waitFor(() => {
      expect(mocks.savePolicy).toHaveBeenCalledWith({
        subject_key: "skill:reviewer",
        kind: "skill",
        mode: "frozen",
        max_canary_basis_points: 1_000,
        expected_version: null,
      });
    });
    expect(screen.queryByText(/全局启用|版本固定/)).not.toBeInTheDocument();
  });

  it("uses the exact durable version and exposes a stale CAS conflict", async () => {
    mocks.policies = [{
      subject_key: "skill:reviewer",
      kind: "skill",
      mode: "manual",
      max_canary_basis_points: 250,
      version: 7,
      updated_at: "2026-08-26T00:00:00+00:00",
    }];
    mocks.saveProblem = {
      status: 409,
      code: "evolution_policy_version_conflict",
      message: "conflict",
      correlationId: "corr-policy",
      retryable: false,
    };
    const user = userEvent.setup();
    render(<EvolutionPolicyPanel />);

    expect(screen.getByText("此则已为他处所改。今已重取新版，请核后再存。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "存此策略" }));
    await waitFor(() => {
      expect(mocks.savePolicy).toHaveBeenCalledWith(expect.objectContaining({
        expected_version: 7,
        mode: "manual",
        max_canary_basis_points: 250,
      }));
    });
  });

  it("keeps policy-only Skills visible, editable, and deterministically ordered", async () => {
    mocks.policies = [{
      subject_key: "skill:archived-helper",
      kind: "skill",
      mode: "manual",
      max_canary_basis_points: 250,
      version: 4,
      updated_at: "2026-08-26T00:00:00+00:00",
    }];
    const user = userEvent.setup();
    render(<EvolutionPolicyPanel />);

    expect(
      screen.getAllByRole("heading", { level: 5 }).map((heading) => heading.textContent),
    ).toEqual(["archived-helper", "reviewer"]);

    const policyOnlyRow = screen.getByRole("article", { name: "archived-helper" });
    expect(within(policyOnlyRow).getByText("Skill Loader 今不可取用")).toBeInTheDocument();
    expect(within(policyOnlyRow).getByText("Skill 来源今不可取")).toBeInTheDocument();
    expect(within(policyOnlyRow).getByText("策展护持未录")).toBeInTheDocument();

    await user.click(within(policyOnlyRow).getByRole("button", { name: "存此策略" }));
    await waitFor(() => {
      expect(mocks.savePolicy).toHaveBeenCalledWith({
        subject_key: "skill:archived-helper",
        kind: "skill",
        mode: "manual",
        max_canary_basis_points: 250,
        expected_version: 4,
      });
    });
  });

  it("does not request the admin policy contract for an API-only principal", () => {
    mocks.admin = false;
    render(<EvolutionPolicyPanel />);

    expect(screen.getByText("阅改明载演化之则，须有管理员之权。")).toBeInTheDocument();
    expect(mocks.enabledValues[mocks.enabledValues.length - 1]).toBe(false);
    expect(screen.queryByRole("button", { name: "存此策略" })).not.toBeInTheDocument();
  });

  it("keeps the center usable when an admin policy read is denied", () => {
    mocks.queryProblem = {
      status: 403,
      code: "insufficient_scope",
      message: "denied",
      correlationId: "corr-denied",
      retryable: false,
    };
    render(<EvolutionPolicyPanel />);

    expect(screen.getByText("阅改明载演化之则，须有管理员之权。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "存此策略" })).not.toBeInTheDocument();
  });
});
