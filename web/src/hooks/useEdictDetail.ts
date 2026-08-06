import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { isApiProblem, toApiProblem } from "../api/client";
import {
  getEdictDetailSnapshot,
  getEdictEvents,
  replayGovernedEdict,
  resolveEdictDecision,
  type EdictDetailSnapshotV1,
  type GovernedReplaySource,
  type ResolveEdictDecisionInput,
} from "../api/edicts";
import { problemPageStatus } from "../components/states/problemPageStatus";
import type { PageDataStatus } from "../contracts/api";
import { POLL_INTERVAL_DETAIL } from "../utils/constants";

export const EDICT_DETAIL_QUERY_KEY = (edictId: string) =>
  ["edict-detail", edictId, "snapshot-v1"] as const;

function isEmpty(snapshot: EdictDetailSnapshotV1): boolean {
  return (
    snapshot.memorials.length === 0 &&
    snapshot.runs.length === 0 &&
    snapshot.decisions.length === 0 &&
    snapshot.evidence.length === 0
  );
}

function asProblem(error: unknown) {
  if (!error) return null;
  return isApiProblem(error) ? error : toApiProblem(error);
}

export function useEdictDetail(edictId: string) {
  const queryClient = useQueryClient();
  const detailQuery = useQuery({
    queryKey: EDICT_DETAIL_QUERY_KEY(edictId),
    queryFn: () => getEdictDetailSnapshot(edictId),
    enabled: !!edictId,
    refetchOnMount: "always",
    refetchInterval: ({ state }) =>
      state.data?.edict.status === "open" ? POLL_INTERVAL_DETAIL : false,
  });

  const eventsQuery = useQuery({
    queryKey: ["events", edictId],
    queryFn: () => getEdictEvents(edictId),
    enabled: !!edictId,
    refetchInterval: () =>
      detailQuery.data?.edict.status === "open" ? POLL_INTERVAL_DETAIL : false,
  });

  const invalidateAuthoritativeConsumers = async () => {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: EDICT_DETAIL_QUERY_KEY(edictId),
      }),
      queryClient.invalidateQueries({ queryKey: ["decisions", edictId] }),
      queryClient.invalidateQueries({ queryKey: ["run-state", edictId] }),
      queryClient.invalidateQueries({ queryKey: ["evidence", edictId] }),
      queryClient.invalidateQueries({
        queryKey: ["control-center", "snapshot-v1"],
      }),
    ]);
  };

  const resolveMutation = useMutation({
    mutationFn: (input: ResolveEdictDecisionInput) =>
      resolveEdictDecision(input),
    onSuccess: invalidateAuthoritativeConsumers,
  });
  const replayMutation = useMutation({
    mutationFn: (source: GovernedReplaySource) => replayGovernedEdict(source),
    onSuccess: async () => {
      await Promise.all([
        invalidateAuthoritativeConsumers(),
        queryClient.invalidateQueries({ queryKey: ["edicts"] }),
      ]);
    },
  });

  // Events remain a legacy activity feed; they never redefine the composed governance truth.
  const problem = asProblem(detailQuery.error);
  const detail = detailQuery.data ?? null;
  let status: PageDataStatus;
  if (problem && detail) status = "stale";
  else if (problem) status = problemPageStatus(problem);
  else if (!detail) status = "loading";
  else status = isEmpty(detail) ? "success-empty" : "success-data";

  return {
    detail,
    edict: detail?.edict ?? null,
    memorials: detail?.memorials ?? [],
    events: eventsQuery.data?.data ?? [],
    status,
    problem,
    isLoading: status === "loading",
    error: problem,
    refetch: () => {
      void detailQuery.refetch();
      void eventsQuery.refetch();
    },
    resolveDecision: (input: ResolveEdictDecisionInput) =>
      resolveMutation.mutateAsync(input),
    replay: (source: GovernedReplaySource) =>
      replayMutation.mutateAsync(source),
  };
}
