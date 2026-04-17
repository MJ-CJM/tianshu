import { useQuery, useQueries, useMutation, useQueryClient } from "@tanstack/react-query";
import { listEdicts, getEdictMemorial } from "../api/edicts";
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

export function useEdictLatestMemorials(edictIds: string[]) {
  return useQueries({
    queries: edictIds.map((id) => ({
      queryKey: ["memorial_latest", id],
      queryFn: () => getEdictMemorial(id),
      refetchInterval: 5_000,
    })),
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
