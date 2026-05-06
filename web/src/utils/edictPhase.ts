import type { Memorial } from "../api/types";

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

export const PHASE_COLORS: Record<EdictPhase, string> = {
  running: "#1890ff",
  needs_review: "#fa8c16",
  idle: "#52c41a",
  no_memorial: "#faad14",
};

export const PHASE_SORT_ORDER: Record<EdictPhase, number> = {
  needs_review: 0,
  running: 1,
  idle: 2,
  no_memorial: 3,
};
