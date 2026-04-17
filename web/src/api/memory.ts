import type { ApiResponse, MemoryEntry, EdictMemorialGroup } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || "";

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, init);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getPersonaMemory(
  personaId: string,
  limit = 50,
): Promise<MemoryEntry[]> {
  const resp = await fetchJson<ApiResponse<MemoryEntry[]>>(
    `/api/memory/${personaId}?limit=${limit}`,
  );
  return resp.data ?? [];
}

export async function recallMemory(query: {
  persona_id: string;
  query?: string;
  category?: string;
  limit?: number;
  include_shared?: boolean;
}): Promise<MemoryEntry[]> {
  const resp = await fetchJson<ApiResponse<MemoryEntry[]>>(
    `/api/memory/recall`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(query),
    },
  );
  return resp.data ?? [];
}

export async function deleteMemoryEntry(entryId: string): Promise<void> {
  await fetchJson(`/api/memory/${entryId}`, { method: "DELETE" });
}

export async function deleteMemoryEntries(
  entryIds: string[],
): Promise<{ deleted: number }> {
  const resp = await fetchJson<ApiResponse<{ deleted: number }>>(
    `/api/memory/batch-delete`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entry_ids: entryIds }),
    },
  );
  return resp.data ?? { deleted: 0 };
}

export async function getPersonaMemorials(
  personaId: string,
  limit = 100,
): Promise<EdictMemorialGroup[]> {
  const resp = await fetchJson<ApiResponse<EdictMemorialGroup[]>>(
    `/api/memorials/by-persona/${personaId}?limit=${limit}`,
  );
  return resp.data ?? [];
}

export async function getMemoryPolicies(): Promise<
  Record<string, { persona_id: string; can_read: string[]; can_write: string[]; share_level: string }>
> {
  const resp = await fetchJson<ApiResponse<Record<string, unknown>>>(
    `/api/memory/policies`,
  );
  return (resp.data ?? {}) as any;
}
