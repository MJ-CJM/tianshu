import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getPersonaMemory,
  recallMemory,
  deleteMemoryEntry,
  deleteMemoryEntries,
  getMemoryPolicies,
} from "../api/memory";

export function usePersonaMemory(personaId: string, limit = 50) {
  return useQuery({
    queryKey: ["memory", personaId, limit],
    queryFn: () => getPersonaMemory(personaId, limit),
    enabled: !!personaId,
  });
}

export function useRecallMemory() {
  return useMutation({
    mutationFn: recallMemory,
  });
}

export function useDeleteMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteMemoryEntry,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memory"] });
    },
  });
}

export function useBatchDeleteMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteMemoryEntries,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memory"] });
    },
  });
}

export function useMemoryPolicies() {
  return useQuery({
    queryKey: ["memory", "policies"],
    queryFn: getMemoryPolicies,
  });
}
