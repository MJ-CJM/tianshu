import apiClient from "./client";
import type { ApiResponse } from "./types";

export interface ProfileFrontmatter {
  persona_id: string;
  persona_name: string;
  version: number;
  last_synthesized: string;
  synthesizer_model: string;
  data_window: string;
  data_sources: Record<string, number>;
  manually_edited: boolean;
  degraded: boolean;
}

export interface ProfilePayload {
  persona_id: string;
  exists: boolean;
  frontmatter: ProfileFrontmatter | null;
  markdown: string;
  auto_section?: string;
  manual_section?: string;
  history: { name: string; mtime: number }[];
}

export async function getPersonaProfile(
  id: string,
): Promise<ApiResponse<ProfilePayload>> {
  const { data } = await apiClient.get<ApiResponse<ProfilePayload>>(
    `/personas/${id}/profile`,
  );
  return data;
}

export interface ProfileHistoryItem {
  persona_id: string;
  version: number;
  name: string;
  markdown: string;
}

export async function getProfileHistory(
  id: string,
  version: number,
): Promise<ApiResponse<ProfileHistoryItem>> {
  const { data } = await apiClient.get<ApiResponse<ProfileHistoryItem>>(
    `/personas/${id}/profile/history/${version}`,
  );
  return data;
}

export async function updateProfileManual(
  id: string,
  manualSection: string,
): Promise<ApiResponse<{ persona_id: string; manual_updated: boolean }>> {
  const { data } = await apiClient.put<
    ApiResponse<{ persona_id: string; manual_updated: boolean }>
  >(`/personas/${id}/profile/manual`, { manual_section: manualSection });
  return data;
}
