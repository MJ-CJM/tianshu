import type { ApiResponse, MemoryEntry, EdictMemorialGroup } from "./types";
import apiClient from "./client";

interface MemoryPolicy {
  persona_id: string;
  can_read: string[];
  can_write: string[];
  share_level: string;
}

export async function getPersonaMemory(
  personaId: string,
  limit = 50,
): Promise<MemoryEntry[]> {
  const { data } = await apiClient.get<ApiResponse<MemoryEntry[]>>(
    `/memory/${encodeURIComponent(personaId)}`,
    { params: { limit } },
  );
  return data.data ?? [];
}

export async function recallMemory(query: {
  persona_id: string;
  query?: string;
  category?: string;
  limit?: number;
  include_shared?: boolean;
}): Promise<MemoryEntry[]> {
  const { data } = await apiClient.post<ApiResponse<MemoryEntry[]>>(
    "/memory/recall",
    query,
  );
  return data.data ?? [];
}

export async function deleteMemoryEntry(entryId: string): Promise<void> {
  await apiClient.delete(`/memory/${encodeURIComponent(entryId)}`);
}

export async function deleteMemoryEntries(
  entryIds: string[],
): Promise<{ deleted: number }> {
  const { data } = await apiClient.post<ApiResponse<{ deleted: number }>>(
    "/memory/batch-delete",
    { entry_ids: entryIds },
  );
  return data.data ?? { deleted: 0 };
}

export async function getPersonaMemorials(
  personaId: string,
  limit = 100,
): Promise<EdictMemorialGroup[]> {
  const { data } = await apiClient.get<ApiResponse<EdictMemorialGroup[]>>(
    `/memorials/by-persona/${encodeURIComponent(personaId)}`,
    { params: { limit } },
  );
  return data.data ?? [];
}

export async function getMemoryPolicies(): Promise<
  Record<string, MemoryPolicy>
> {
  const { data } = await apiClient.get<ApiResponse<Record<string, MemoryPolicy>>>(
    "/memory/policies",
  );
  return data.data ?? {};
}
