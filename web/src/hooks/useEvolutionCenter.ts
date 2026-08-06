import { useQuery } from "@tanstack/react-query";

import {
  getEvolutionCenterSnapshot,
  type EvolutionCenterSnapshotV1,
} from "../api/evolution";
import { isApiProblem, toApiProblem } from "../api/client";
import { problemPageStatus } from "../components/states/problemPageStatus";
import type { PageDataStatus } from "../contracts/api";

export const EVOLUTION_CENTER_QUERY_KEY = [
  "evolution-center",
  "snapshot-v1",
] as const;

export function isEvolutionSnapshotEmpty(
  snapshot: EvolutionCenterSnapshotV1,
): boolean {
  return (
    snapshot.status === "enabled" &&
    snapshot.candidates.length === 0 &&
    snapshot.routing.length === 0
  );
}

export function useEvolutionCenter() {
  const query = useQuery({
    queryKey: EVOLUTION_CENTER_QUERY_KEY,
    queryFn: getEvolutionCenterSnapshot,
    refetchOnMount: "always",
  });
  const problem = query.error
    ? isApiProblem(query.error)
      ? query.error
      : toApiProblem(query.error)
    : null;
  const data = query.data ?? null;
  let status: PageDataStatus;
  if (problem && data) status = "stale";
  else if (problem) status = problemPageStatus(problem);
  else if (!data) status = "loading";
  else
    status = isEvolutionSnapshotEmpty(data) ? "success-empty" : "success-data";

  return {
    data,
    status,
    problem,
    refetch: () => void query.refetch(),
  };
}
