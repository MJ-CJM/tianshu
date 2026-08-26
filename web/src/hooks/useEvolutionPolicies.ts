import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listEvolutionPolicies,
  putEvolutionPolicy,
  type UpsertEvolutionPolicyV1,
} from "../api/evolution";
import { isApiProblem, toApiProblem } from "../api/client";

export const EVOLUTION_POLICIES_QUERY_KEY = [
  "evolution-center",
  "policies-v1",
] as const;

export function useEvolutionPolicies(enabled: boolean) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: EVOLUTION_POLICIES_QUERY_KEY,
    queryFn: listEvolutionPolicies,
    enabled,
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: (policy: UpsertEvolutionPolicyV1) => putEvolutionPolicy(policy),
    onSuccess: (policy) => {
      queryClient.setQueryData(
        EVOLUTION_POLICIES_QUERY_KEY,
        (current: Awaited<ReturnType<typeof listEvolutionPolicies>> | undefined) => {
          const remaining = (current ?? []).filter(
            (item) => item.subject_key !== policy.subject_key,
          );
          return [...remaining, policy].sort((left, right) =>
            left.subject_key.localeCompare(right.subject_key),
          );
        },
      );
    },
    onError: (error) => {
      const problem = isApiProblem(error) ? error : toApiProblem(error);
      if (problem.status === 409) {
        void queryClient.invalidateQueries({ queryKey: EVOLUTION_POLICIES_QUERY_KEY });
      }
    },
  });

  return {
    policies: query.data ?? [],
    isLoading: enabled && query.isPending,
    problem: query.error
      ? isApiProblem(query.error)
        ? query.error
        : toApiProblem(query.error)
      : null,
    savePolicy: mutation.mutateAsync,
    savingSubjectKey: mutation.isPending
      ? mutation.variables?.subject_key ?? null
      : null,
    saveProblem: mutation.error
      ? isApiProblem(mutation.error)
        ? mutation.error
        : toApiProblem(mutation.error)
      : null,
  };
}
