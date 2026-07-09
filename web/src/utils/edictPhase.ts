import type { Memorial } from "../api/types";
import { useT } from "../i18n";

export type EdictPhase = "running" | "needs_review" | "idle" | "no_memorial";

export function deriveEdictPhase(memorial: Memorial | null): EdictPhase {
  if (!memorial) return "no_memorial";
  if (memorial.review_status === "pending" || memorial.status === "needs_review")
    return "needs_review";
  if (
    memorial.status === "submitted" ||
    memorial.status === "running" ||
    memorial.status === "planning" ||
    memorial.status === "auditing" ||
    memorial.status === "scheduled"
  )
    return "running";
  return "idle";
}

export const PHASE_LABELS: Record<EdictPhase, string> = {
  running: "运行中",
  needs_review: "待朱批",
  idle: "待批示",
  no_memorial: "待启动",
};

// CSS 变量引用,随浅/深主题切换;展示配合 SemanticTag 使用
export const PHASE_COLORS: Record<EdictPhase, string> = {
  running: "var(--ts-status-running)",
  needs_review: "var(--ts-status-needs-review)",
  idle: "var(--ts-status-completed)",
  no_memorial: "var(--ts-status-submitted)",
};

export const PHASE_SORT_ORDER: Record<EdictPhase, number> = {
  needs_review: 0,
  running: 1,
  idle: 2,
  no_memorial: 3,
};

/**
 * Locale-aware phase labels. Static `PHASE_LABELS` is preserved as zh-classic
 * fallback for legacy call sites that cannot use hooks.
 */
export function useEdictPhaseLabels(): Record<EdictPhase, string> {
  const t = useT();
  return {
    running: t("phase.running"),
    needs_review: t("phase.needs_review"),
    idle: t("phase.idle"),
    no_memorial: t("phase.no_memorial"),
  };
}
