import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { WsMessage } from "../api/types";

const MEMORIAL_INVALIDATION_TYPES = new Set([
  "execution.completed",
  "execution.failed",
  "audit.completed",
]);

const EDICT_INVALIDATION_TYPES = new Set(["edict.submitted"]);

const DAG_INVALIDATION_TYPES = new Set([
  "dag.started",
  "dag.node.completed",
  "dag.node.failed",
  "dag.cancelled",
]);

const CONSULTATION_INVALIDATION_TYPES = new Set([
  "consultation.completed",
  "consultation.failed",
]);

const TOOL_APPROVAL_INVALIDATION_TYPES = new Set([
  "tool.approval_required",
  "decree.approved",
  "decree.rejected",
]);

const CONTROL_CENTER_INVALIDATION_TYPES = new Set([
  "edict.submitted",
  "edict.updated",
  "edict.closed",
  "edict.scheduled",
  "edict.lifecycle.changed",
  "edict.resume",
  "plan.completed",
  "execution.started",
  "execution.completed",
  "execution.failed",
  "execution.cancelled",
  "audit.completed",
  "tool.approval_required",
  "decree.approved",
  "decree.rejected",
  "decree.retry",
  "decree.cancelled",
  "decree.guided",
  "outer_loop.started",
  "outer_loop.completed",
  "outer_loop.exhausted",
  "outer_loop.escalated",
  "outer_loop.approval.requested",
  "outer_loop.approval.received",
  "outer_loop.resumed",
]);

type WsSubscribe = (listener: (message: WsMessage) => void) => () => void;

export function useWsQueryInvalidation(subscribe: WsSubscribe): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    return subscribe((message) => {
      const { type, edict_id } = message;

      if (MEMORIAL_INVALIDATION_TYPES.has(type)) {
        if (edict_id) {
          queryClient.invalidateQueries({ queryKey: ["memorials", edict_id] });
          queryClient.invalidateQueries({ queryKey: ["events", edict_id] });
          queryClient.invalidateQueries({ queryKey: ["edict", edict_id] });
          queryClient.invalidateQueries({
            queryKey: ["memorial_latest", edict_id],
          });
        }
        queryClient.invalidateQueries({ queryKey: ["edicts"] });
        queryClient.invalidateQueries({ queryKey: ["needs_review"] });
      }

      if (EDICT_INVALIDATION_TYPES.has(type)) {
        queryClient.invalidateQueries({ queryKey: ["edicts"] });
        queryClient.invalidateQueries({ queryKey: ["memorial_latest"] });
        queryClient.invalidateQueries({ queryKey: ["scheduler_jobs"] });
      }

      if (DAG_INVALIDATION_TYPES.has(type)) {
        queryClient.invalidateQueries({ queryKey: ["dag"] });
        queryClient.invalidateQueries({ queryKey: ["workers"] });
        if (edict_id) {
          queryClient.invalidateQueries({
            queryKey: ["dag", "by-edict", edict_id],
          });
        }
      }

      if (CONSULTATION_INVALIDATION_TYPES.has(type)) {
        queryClient.invalidateQueries({ queryKey: ["consultation"] });
      }

      if (TOOL_APPROVAL_INVALIDATION_TYPES.has(type)) {
        queryClient.invalidateQueries({
          queryKey: ["approvals", "pending_tool_calls"],
        });
        if (edict_id) {
          queryClient.invalidateQueries({
            queryKey: ["policy_events", edict_id],
          });
        }
      }

      if (CONTROL_CENTER_INVALIDATION_TYPES.has(type)) {
        queryClient.invalidateQueries({
          queryKey: ["control-center", "snapshot-v1"],
        });
      }
    });
  }, [queryClient, subscribe]);
}
