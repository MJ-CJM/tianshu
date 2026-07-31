import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getDagByEdict, getDag, cancelDag, retryDag, getWorkersStatus } from '../api/dag';
import { isApiProblem } from '../api/client';

export function dagByEdictPollInterval(data: unknown, error: unknown): number | false {
  if (data === null) return false;
  return isApiProblem(error) && error.status === 404 ? false : 3000;
}

export function useDagByEdict(edictId: string | undefined) {
  return useQuery({
    queryKey: ['dag', 'by-edict', edictId],
    queryFn: () => getDagByEdict(edictId!),
    enabled: !!edictId,
    refetchInterval: (query) =>
      dagByEdictPollInterval(query.state.data, query.state.error),
    select: (data) => data?.data ?? null,
    retry: false,
  });
}

export function useDag(dagId: string | undefined) {
  return useQuery({
    queryKey: ['dag', dagId],
    queryFn: () => getDag(dagId!),
    enabled: !!dagId,
    refetchInterval: 3000,
    select: (data) => data.data,
  });
}

export function useWorkersStatus() {
  return useQuery({
    queryKey: ['workers', 'status'],
    queryFn: () => getWorkersStatus(),
    refetchInterval: 5000,
    select: (data) => data.data,
  });
}

export function useCancelDag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (dagId: string) => cancelDag(dagId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dag'] });
    },
  });
}

export function useRetryDag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ dagId, fromNodeIds }: { dagId: string; fromNodeIds?: string[] }) =>
      retryDag(dagId, fromNodeIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dag'] });
    },
  });
}
