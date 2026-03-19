import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getCostSummary,
  getCostRecords,
  getCostBudget,
  setCostBudget,
} from "../api/cost";

export function useCostSummary(period?: string, edictId?: string) {
  return useQuery({
    queryKey: ["cost", "summary", period, edictId],
    queryFn: () => getCostSummary(period, edictId),
  });
}

export function useCostRecords(edictId?: string, limit = 50, offset = 0) {
  return useQuery({
    queryKey: ["cost", "records", edictId, limit, offset],
    queryFn: () => getCostRecords(edictId, limit, offset),
  });
}

export function useCostBudget(scope = "global") {
  return useQuery({
    queryKey: ["cost", "budget", scope],
    queryFn: () => getCostBudget(scope),
  });
}

export function useSetCostBudget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      scope,
      budgetCny,
      period,
    }: {
      scope: string;
      budgetCny: number;
      period?: string;
    }) => setCostBudget(scope, budgetCny, period),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cost"] });
    },
  });
}
