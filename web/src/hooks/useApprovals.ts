import { useQuery } from "@tanstack/react-query";
import { listEdicts, getLatestMemorialsBatch } from "../api/edicts";
import {
  listNeedsReview,
  fetchPendingToolCalls,
} from "../api/decrees";

export function useNeedsReview(limit = 50) {
  return useQuery({
    queryKey: ["needs_review", limit],
    queryFn: () => listNeedsReview({ limit }),
    refetchInterval: 10_000,
  });
}

export function useOpenEdicts(limit = 100, enabled = true) {
  return useQuery({
    queryKey: ["edicts", "open"],
    queryFn: () => listEdicts({ status: "open", limit }),
    refetchInterval: 10_000,
    enabled,
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

export function usePendingToolCalls(enabled = true) {
  return useQuery({
    queryKey: ["approvals", "pending_tool_calls"],
    queryFn: fetchPendingToolCalls,
    refetchInterval: 5_000,
    enabled,
  });
}
