import apiClient from "./client";
import type {
  ApiResponse,
  EventBusHandlers,
  EventBusStats,
  RecentEvent,
  HooksRegistry,
  NotificationChannel,
  MemoryStats,
  PromptLayersResponse,
  RoutingRules,
  AuditRulesResponse,
  CompactionResult,
  ReflectionResult,
  PlannerStats,
} from "./types";

export async function getEventBusHandlers(): Promise<ApiResponse<EventBusHandlers>> {
  const { data } = await apiClient.get<ApiResponse<EventBusHandlers>>("/event-bus/handlers");
  return data;
}

export async function getEventBusStats(): Promise<ApiResponse<EventBusStats>> {
  const { data } = await apiClient.get<ApiResponse<EventBusStats>>("/event-bus/stats");
  return data;
}

export async function getRecentEvents(limit = 50): Promise<ApiResponse<RecentEvent[]>> {
  const { data } = await apiClient.get<ApiResponse<RecentEvent[]>>("/event-bus/recent", {
    params: { limit },
  });
  return data;
}

export async function getHooksRegistry(): Promise<ApiResponse<HooksRegistry>> {
  const { data } = await apiClient.get<ApiResponse<HooksRegistry>>("/hooks/registry");
  return data;
}

export async function getNotificationChannels(): Promise<ApiResponse<NotificationChannel[]>> {
  const { data } = await apiClient.get<ApiResponse<NotificationChannel[]>>("/notifications/channels");
  return data;
}

export async function getMemoryStats(): Promise<ApiResponse<MemoryStats>> {
  const { data } = await apiClient.get<ApiResponse<MemoryStats>>("/memory/stats");
  return data;
}

export async function compactMemory(personaId: string, maxAgeDays = 7): Promise<ApiResponse<CompactionResult>> {
  const { data } = await apiClient.post<ApiResponse<CompactionResult>>("/memory/compact", {
    persona_id: personaId,
    max_age_days: maxAgeDays,
  });
  return data;
}

export async function triggerReflection(personaId: string): Promise<ApiResponse<ReflectionResult>> {
  const { data } = await apiClient.post<ApiResponse<ReflectionResult>>("/memory/reflect", {
    persona_id: personaId,
  });
  return data;
}

export async function getPromptLayers(personaId: string): Promise<ApiResponse<PromptLayersResponse>> {
  const { data } = await apiClient.get<ApiResponse<PromptLayersResponse>>(`/system-prompt/layers/${personaId}`);
  return data;
}

export async function getRoutingRules(): Promise<ApiResponse<RoutingRules>> {
  const { data } = await apiClient.get<ApiResponse<RoutingRules>>("/routing/rules");
  return data;
}

export async function getAuditRules(): Promise<ApiResponse<AuditRulesResponse>> {
  const { data } = await apiClient.get<ApiResponse<AuditRulesResponse>>("/audit/rules");
  return data;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function getWorkersStatus(): Promise<ApiResponse<any>> {
  const { data } = await apiClient.get("/workers/status");
  return data;
}

export async function getPlannerStats(): Promise<ApiResponse<PlannerStats>> {
  const { data } = await apiClient.get<ApiResponse<PlannerStats>>("/planner/stats");
  return data;
}
