import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { WsMessage } from "../api/types";

const MEMORIAL_INVALIDATION_TYPES = new Set([
  "execution.completed",
  "execution.failed",
  "audit.completed",
]);

const EDICT_INVALIDATION_TYPES = new Set([
  "edict.submitted",
]);

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

export function useWsQueryInvalidation(lastMessage: WsMessage | null): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!lastMessage) return;

    const { type, edict_id } = lastMessage;

    if (MEMORIAL_INVALIDATION_TYPES.has(type)) {
      if (edict_id) {
        queryClient.invalidateQueries({ queryKey: ["memorials", edict_id] });
        queryClient.invalidateQueries({ queryKey: ["events", edict_id] });
        queryClient.invalidateQueries({ queryKey: ["edict", edict_id] });
        queryClient.invalidateQueries({ queryKey: ["memorial_latest", edict_id] });
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
        queryClient.invalidateQueries({ queryKey: ["dag", "by-edict", edict_id] });
      }
    }

    if (CONSULTATION_INVALIDATION_TYPES.has(type)) {
      queryClient.invalidateQueries({ queryKey: ["consultation"] });
    }
  }, [lastMessage, queryClient]);
}
