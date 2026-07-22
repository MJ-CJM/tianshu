import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import zhClassic from "./locales/zh-classic.json";
import zhModern from "./locales/zh-modern.json";

type StringEntry = {
  path: string;
  value: string;
};

function stringEntries(value: unknown, path = ""): StringEntry[] {
  if (typeof value === "string") return [{ path, value }];
  if (!value || typeof value !== "object") return [];

  return Object.entries(value).flatMap(([key, child]) =>
    stringEntries(child, path ? `${path}.${key}` : key),
  );
}

function valueAt(locale: unknown, path: string): unknown {
  return stringEntries(locale).find((entry) => entry.path === path)?.value;
}

const ENGLISH_GOVERNANCE_TERMS: Record<string, string> = {
  "nav.approvals": "Decisions",
  "action.approve": "Allow",
  "action.review": "Decide",
  "status.needs_review": "Pending Decision",
  "phase.needs_review": "Pending Decision",
  "event.label.plan.pending_review": "Plan Pending Decision",
  "event.label.plan.approved": "Plan Decision: Allowed",
  "event.label.decree.approved": "Decision: Allowed",
  "event.decree.approved": "Decision: Allowed",
  "memorial.review.pending": "Pending Decision",
  "pendingTool.title": "Tool call pending decision",
  "pendingTool.approve": "Allow",
  "pendingTool.action": "Allow/Reject",
  "decree.actionOption.approve": "Allow — Confirm execution",
  "page.edictDetail.planApprove": "Allow (execute plan)",
  "page.edictDetail.decreePrompt": "Run pending decision, choose an action:",
  "page.sessionRules.alertTitle": "Session Rules record reusable tool decisions",
  "page.sessionRules.alertDesc":
    "When allowing a tool call in the Decision Center with scope \"This task\" or \"Global\", a session rule is automatically created. You can also add rules manually to pre-authorize permissions. Note: shell_exec/bash tools cannot receive global authorization.",
  "page.sessionRules.addModalTitle": "Add Pre-Authorization Rule",
  "page.sessionRules.empty":
    "No rules — click \"Add Rule\" in the top right to create manually, or select \"This task / Global\" when allowing tool calls to auto-generate",
  "form.edict.field.planReview": "Plan Decision",
  "form.edict.tooltip.planReview":
    "When enabled, plans require a human decision before execution",
  "sessionRules.source.approval": "Decision-granted",
  "sessionRules.sourceFilter.approval": "Decision-granted",
  "sessionRules.popconfirm.description":
    "After revocation, related tool calls will require a decision again",
  "toast.toolApproved": "Allowed {tool}",
  "toast.toolApprovedWithRule": "Allowed and saved session rule ({scope})",
  "toast.planApproved": "Plan allowed, starting execution",
  "toast.planApproveFailed": "Plan decision failed",
  "comp.policyTimeline.filterApproval":
    "Tool decisions (tool.approval_required)",
  "comp.network.allowWrite":
    "Allow write methods (POST/PUT/DELETE/PATCH) — requires a decision",
  "comp.policyProfile.autoApproveLabel": "Governed auto-decision up to tier",
  "comp.policyProfile.autoApproveTooltip":
    "Rules automatically decide to allow tools at tier ≤ this value without requesting a human decision",
  "comp.policyToast.approvalRequired": "Decision required",
  "tongzheng.field.homeChannelExtra":
    "Fallback chat_id for cron-triggered results and source-less task decisions",
  "tongzheng.tg.alert.tip4":
    "Home channel is the fallback chat for cron results / source-less decisions (chat_id, negative for groups)",
  "tongzheng.tg.field.homeChannelExtra":
    "Fallback chat_id for cron / source-less decisions",
  "system.mcp.create.defaultTierHint":
    "Per-tool tier. T2 = network, requires a decision. T0 = readonly, fast path.",
};

describe("governance terminology contract", () => {
  it("keeps all three locale key structures identical", () => {
    const anchorPaths = stringEntries(zhClassic)
      .map(({ path }) => path)
      .sort();

    expect(stringEntries(zhModern).map(({ path }) => path).sort()).toEqual(
      anchorPaths,
    );
    expect(stringEntries(en).map(({ path }) => path).sort()).toEqual(anchorPaths);
  });

  it.each([
    ["zh-classic", zhClassic],
    ["zh-modern", zhModern],
    ["en", en],
  ])("preserves compatibility keys in %s", (_, locale) => {
    expect(valueAt(locale, "nav.approvals")).toBeTypeOf("string");
    expect(valueAt(locale, "status.needs_review")).toBeTypeOf("string");
    expect(valueAt(locale, "event.label.decree.approved")).toBeTypeOf("string");
    expect(valueAt(locale, "sessionRules.source.approval")).toBeTypeOf("string");
  });

  it.each([
    ["zh-classic", zhClassic],
    ["zh-modern", zhModern],
  ])("removes historical approval terms from %s user-facing values", (_, locale) => {
    const violations = stringEntries(locale).filter(({ value }) =>
      /批红|朱批|司礼监代批|审批|待批/.test(value),
    );

    expect(violations).toEqual([]);
    expect(valueAt(locale, "comp.policyProfile.autoApproveLabel")).toBe(
      "自动裁决最高 Tier",
    );
    expect(valueAt(locale, "comp.policyProfile.autoApproveTooltip")).toBe(
      "规则将对 Tier ≤ 该值的工具自动作出允许裁决，不再请求人工裁决",
    );
  });

  it("uses decision language for English governance while keeping quality review", () => {
    for (const [path, expected] of Object.entries(ENGLISH_GOVERNANCE_TERMS)) {
      expect(valueAt(en, path), path).toBe(expected);
    }

    expect(en.form.edict.field.reviewPolicy).toBe("Review Policy");
  });
});
