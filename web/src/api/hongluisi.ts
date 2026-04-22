import apiClient from "./client";

export type ProviderSource = "db" | "env" | "none";

export interface EngineStatus {
  providers: Record<string, ProviderSource>; // jina / tavily / firecrawl
  fetch_engines: string[];
  search_providers: string[];
}

export async function getEngineStatus(): Promise<EngineStatus> {
  const { data } = await apiClient.get<EngineStatus>("/hongluisi/engine-status");
  return data;
}
