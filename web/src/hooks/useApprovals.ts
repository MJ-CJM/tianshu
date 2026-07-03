import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listEdicts, getLatestMemorialsBatch } from "../api/edicts";
import {
  listNeedsReview,
  createDecree,
  fetchPendingToolCalls,
  submitToolDecision,
} from "../api/decrees";
import type { DecreeCreateRequest, ToolDecisionRequest } from "../api/types";

export function useNeedsReview(limit = 50) {
  return useQuery({
    queryKey: ["needs_review", limit],
    queryFn: () => listNeedsReview({ limit }),
    refetchInterval: 10_000,
  });
}

export function useOpenEdicts(limit = 100) {
  return useQuery({
    queryKey: ["edicts", "open"],
    queryFn: () => listEdicts({ status: "open", limit }),
    refetchInterval: 10_000,
  });
}

export function useEdictLatestMemorials(edictIds: string[], enabled = true) {
  return useQuery({
    queryKey: ["memorial_latest", edictIds],
    queryFn: () => getLatestMemorialsBatch(edictIds),
    enabled: enabled && edictIds.length > 0,
    refetchInterval: 5_000,
  });
}

export function useCreateDecree() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (req: DecreeCreateRequest) => createDecree(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["needs_review"] });
      queryClient.invalidateQueries({ queryKey: ["edicts"] });
      queryClient.invalidateQueries({ queryKey: ["memorials"] });
      queryClient.invalidateQueries({ queryKey: ["memorial_latest"] });
    },
  });
}

export function usePendingToolCalls() {
  return useQuery({
    queryKey: ["approvals", "pending_tool_calls"],
    queryFn: fetchPendingToolCalls,
    refetchInterval: 5_000,
  });
}

export function useSubmitToolDecision() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (req: ToolDecisionRequest) => submitToolDecision(req),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["approvals", "pending_tool_calls"],
      });
      queryClient.invalidateQueries({ queryKey: ["policy_events"] });
    },
  });
}
