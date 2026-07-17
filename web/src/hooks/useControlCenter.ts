import { useQuery } from "@tanstack/react-query";

import {
  getControlCenterSnapshot,
  type ControlCenterSnapshotV1,
} from "../api/control";
import { isApiProblem, toApiProblem } from "../api/client";
import { problemPageStatus } from "../components/states/problemPageStatus";
import type { PageDataStatus } from "../contracts/api";

export const CONTROL_CENTER_QUERY_KEY = ["control-center", "snapshot-v1"] as const;

function isEmpty(snapshot: ControlCenterSnapshotV1): boolean {
  return (
    snapshot.active_runs.length === 0 &&
    snapshot.pending_decisions.length === 0 &&
    snapshot.recent_evidence.length === 0
  );
}

export function useControlCenter() {
  const query = useQuery({
    queryKey: CONTROL_CENTER_QUERY_KEY,
    queryFn: getControlCenterSnapshot,
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
  else status = isEmpty(data) ? "success-empty" : "success-data";

  return {
    data,
    status,
    problem,
    refetch: () => void query.refetch(),
  };
}
