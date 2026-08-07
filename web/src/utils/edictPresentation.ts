import type { Edict, Memorial, TaskStatus } from "../api/types";

export type EdictTaskKind =
  | "immediate"
  | "scheduled_once"
  | "recurring"
  | "long_running"
  | "conversation"
  | "keqing";

export type EdictWorkspacePhase =
  | TaskStatus
  | "paused"
  | "winding_down"
  | "idle"
  | "no_memorial";

export function getEdictTaskKinds(edict: Edict): EdictTaskKind[] {
  const kinds: EdictTaskKind[] = [];

  if (edict.schedule.type === "once") {
    kinds.push("scheduled_once");
  } else if (edict.schedule.type === "cron" || edict.schedule.type === "interval") {
    kinds.push("recurring");
  } else {
    kinds.push("immediate");
  }

  if (
    edict.acceptance != null ||
    edict.execution_profile === "checkpointed" ||
    edict.execution_profile === "background"
  ) {
    kinds.push("long_running");
  }
  if (edict.runtime.conversation !== false) {
    kinds.push("conversation");
  }
  if (edict.runtime.executor?.startsWith("keqing:")) {
    kinds.push("keqing");
  }

  return kinds;
}

export function deriveEdictWorkspacePhase(
  edict: Edict,
  memorial: Memorial | null,
  pendingDecisionCount = 0,
): EdictWorkspacePhase {
  if (edict.status === "completed") return "completed";
  if (edict.status === "cancelled") return "cancelled";
  if (
    pendingDecisionCount > 0 ||
    memorial?.review_status === "pending" ||
    memorial?.status === "needs_review"
  ) {
    return "needs_review";
  }
  if (edict.runtime.lifecycle_phase === "paused") return "paused";
  if (edict.runtime.lifecycle_phase === "winding_down") return "winding_down";

  if (!memorial) {
    return edict.schedule.type === "immediate" ? "no_memorial" : "scheduled";
  }
  if (memorial.status === "completed") {
    return edict.runtime.conversation !== false ? "idle" : "completed";
  }
  return memorial.status;
}
