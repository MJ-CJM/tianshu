/**
 * Parse orchestrator error code strings into user-friendly messages.
 * Requires `t` (locale-aware) to be passed in by caller.
 *
 * Coverage:
 * - "budget_exhausted: <field> (usage_ratio=<ratio>)" → localized "X budget exhausted (N% used)"
 * - "outer loop exhausted" → "Max iterations reached"
 * - "aborted by human" → "Aborted by user"
 * - "exhausted + aborted" → "Aborted after max iterations"
 * - "checks 配置错*" → "checks misconfigured"
 * - "orchestrator error*" → "Long task runtime error"
 * - Other strings fall through unchanged
 */

import type { TFunction } from "../i18n";

const FIELD_KEY: Record<string, "budgetTokens" | "budgetCost" | "budgetTime"> =
  {
    tokens: "budgetTokens",
    cost: "budgetCost",
    time: "budgetTime",
  };

export interface ParsedError {
  headline: string;
  raw: string;
  isBudget: boolean;
}

const BUDGET_RE =
  /^budget_exhausted(?::\s*(\w+))?\s*(?:\(usage_ratio=([\d.]+)\))?/;

export function parseErrorMessage(
  raw: string | null | undefined,
  t: TFunction,
): ParsedError | null {
  if (!raw) return null;
  const trimmed = raw.trim();

  // budget_exhausted: <field> (usage_ratio=<ratio>)
  const budget = trimmed.match(BUDGET_RE);
  if (budget) {
    const field = budget[1];
    const ratio = budget[2] ? Number(budget[2]) : null;
    const fieldKey = field ? FIELD_KEY[field] : undefined;
    let headline: string;
    if (fieldKey) {
      const base = t(`comp.error.${fieldKey}`);
      const pct =
        ratio != null
          ? t("comp.error.usagePctSuffix", { pct: Math.round(ratio * 100) })
          : "";
      headline = `${base}${pct}`;
    } else {
      headline = t("comp.error.budgetGeneric");
    }
    return { headline, raw, isBudget: true };
  }

  if (trimmed === "outer loop exhausted") {
    return { headline: t("comp.error.exhausted"), raw, isBudget: false };
  }
  if (trimmed === "aborted by human") {
    return { headline: t("comp.error.abortedHuman"), raw, isBudget: false };
  }
  if (trimmed === "exhausted + aborted") {
    return { headline: t("comp.error.exhaustedAbort"), raw, isBudget: false };
  }
  if (
    trimmed.startsWith("checks 配置错") ||
    trimmed.startsWith("checks misconfigured")
  ) {
    return { headline: t("comp.error.checksConfig"), raw, isBudget: false };
  }
  if (trimmed.startsWith("orchestrator error")) {
    return { headline: t("comp.error.longTaskRuntime"), raw, isBudget: false };
  }

  return { headline: trimmed, raw, isBudget: false };
}
