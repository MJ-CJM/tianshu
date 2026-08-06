import apiClient from "./client";

export type ProviderSource = "db" | "env" | "none";

export interface EngineStatus {
  providers: Record<string, ProviderSource>; // jina / tavily / firecrawl
  fetch_engines: string[];
  search_providers: string[];
}

export async function getEngineStatus(): Promise<EngineStatus> {
  const { data } = await apiClient.get<EngineStatus>(
    "/hongluisi/engine-status",
  );
  return data;
}

export interface EnginePreferences {
  fetch_chain: string[];
  search_provider: string | null;
  fallback_mode: string | null;
  scrapling_dynamic_enabled: boolean;
  scrapling_stealthy_enabled: boolean;
}

export async function getEnginePreferences(): Promise<EnginePreferences> {
  const { data } = await apiClient.get<EnginePreferences>(
    "/hongluisi/engine-preferences",
  );
  return data;
}

export async function updateEnginePreferences(
  prefs: EnginePreferences,
): Promise<EnginePreferences> {
  const { data } = await apiClient.patch<EnginePreferences>(
    "/hongluisi/engine-preferences",
    prefs,
  );
  return data;
}
