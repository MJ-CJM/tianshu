import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getEventBusHandlers,
  getEventBusStats,
  getRecentEvents,
  getHooksRegistry,
  getNotificationChannels,
  getMemoryStats,
  compactMemory,
  triggerReflection,
  getPromptLayers,
  getRoutingRules,
  getAuditRules,
  getWorkersStatus,
  getPlannerStats,
} from "../api/ops";

export function useEventBusHandlers() {
  return useQuery({
    queryKey: ["eventBusHandlers"],
    queryFn: async () => {
      const resp = await getEventBusHandlers();
      return resp.data;
    },
    staleTime: 30000,
  });
}

export function useEventBusStats() {
  return useQuery({
    queryKey: ["eventBusStats"],
    queryFn: async () => {
      const resp = await getEventBusStats();
      return resp.data;
    },
    staleTime: 10000,
  });
}

export function useRecentEvents(limit = 50) {
  return useQuery({
    queryKey: ["recentEvents", limit],
    queryFn: async () => {
      const resp = await getRecentEvents(limit);
      return resp.data;
    },
    refetchInterval: 5000,
  });
}

export function useHooksRegistry() {
  return useQuery({
    queryKey: ["hooksRegistry"],
    queryFn: async () => {
      const resp = await getHooksRegistry();
      return resp.data;
    },
    staleTime: 30000,
  });
}

export function useWorkersStatus() {
  return useQuery({
    queryKey: ["workersStatus"],
    queryFn: async () => {
      const resp = await getWorkersStatus();
      return resp.data;
    },
    refetchInterval: 3000,
  });
}

export function useNotificationChannels() {
  return useQuery({
    queryKey: ["notificationChannels"],
    queryFn: async () => {
      const resp = await getNotificationChannels();
      return resp.data;
    },
    staleTime: 30000,
  });
}

export function useMemoryStats() {
  return useQuery({
    queryKey: ["memoryStats"],
    queryFn: async () => {
      const resp = await getMemoryStats();
      return resp.data;
    },
    staleTime: 30000,
  });
}

export function useCompactMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      personaId,
      maxAgeDays,
    }: {
      personaId: string;
      maxAgeDays?: number;
    }) => compactMemory(personaId, maxAgeDays),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memoryStats"] });
    },
  });
}

export function useTriggerReflection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (personaId: string) => triggerReflection(personaId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memoryStats"] });
    },
  });
}

export function usePromptLayers(personaId: string | null) {
  return useQuery({
    queryKey: ["promptLayers", personaId],
    queryFn: async () => {
      if (!personaId) return null;
      const resp = await getPromptLayers(personaId);
      return resp.data;
    },
    enabled: !!personaId,
    staleTime: 30000,
  });
}

export function useRoutingRules() {
  return useQuery({
    queryKey: ["routingRules"],
    queryFn: async () => {
      const resp = await getRoutingRules();
      return resp.data;
    },
    staleTime: 60000,
  });
}

export function useAuditRules() {
  return useQuery({
    queryKey: ["auditRules"],
    queryFn: async () => {
      const resp = await getAuditRules();
      return resp.data;
    },
    staleTime: 60000,
  });
}

export function usePlannerStats() {
  return useQuery({
    queryKey: ["plannerStats"],
    queryFn: async () => {
      const resp = await getPlannerStats();
      return resp.data;
    },
    staleTime: 30000,
  });
}
