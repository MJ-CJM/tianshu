import apiClient from "./client";
import type {
  ApiResponse,
  SkillInfo,
  SkillDetail,
  ToolInfo,
  PromptFilesResponse,
  PromptFileContent,
} from "./types";

// --- Skills ---

export async function listSkills(): Promise<ApiResponse<SkillInfo[]>> {
  const { data } = await apiClient.get<ApiResponse<SkillInfo[]>>("/skills");
  return data;
}

export async function getSkill(name: string): Promise<ApiResponse<SkillDetail>> {
  const { data } = await apiClient.get<ApiResponse<SkillDetail>>(
    `/skills/${encodeURIComponent(name)}`,
  );
  return data;
}

export async function updateSkill(
  name: string,
  content: string,
): Promise<ApiResponse<SkillDetail>> {
  const { data } = await apiClient.put<ApiResponse<SkillDetail>>(
    `/skills/${encodeURIComponent(name)}`,
    { content },
  );
  return data;
}

export async function createSkill(
  name: string,
  content: string,
): Promise<ApiResponse<SkillDetail>> {
  const { data } = await apiClient.post<ApiResponse<SkillDetail>>("/skills", {
    name,
    content,
  });
  return data;
}

export async function deleteSkill(
  name: string,
): Promise<ApiResponse<{ name: string }>> {
  const { data } = await apiClient.delete<ApiResponse<{ name: string }>>(
    `/skills/${encodeURIComponent(name)}`,
  );
  return data;
}

export async function archiveSkill(
  name: string,
): Promise<ApiResponse<{ name: string }>> {
  const { data } = await apiClient.post<ApiResponse<{ name: string }>>(
    `/skills/${encodeURIComponent(name)}/archive`,
  );
  return data;
}

export async function pinSkill(
  name: string,
  pinned: boolean,
): Promise<ApiResponse<{ name: string; pinned: boolean }>> {
  const { data } = await apiClient.post<ApiResponse<{ name: string; pinned: boolean }>>(
    `/skills/${encodeURIComponent(name)}/pin`,
    { pinned },
  );
  return data;
}

// --- Tools ---

export async function listTools(): Promise<ApiResponse<ToolInfo[]>> {
  const { data } = await apiClient.get<ApiResponse<ToolInfo[]>>("/tools");
  return data;
}

export async function setToolEnabled(
  name: string,
  enabled: boolean,
): Promise<ApiResponse<{ name: string; enabled: boolean }>> {
  const { data } = await apiClient.patch<
    ApiResponse<{ name: string; enabled: boolean }>
  >(`/tools/${encodeURIComponent(name)}`, { enabled });
  return data;
}

// --- System Prompt ---

export async function listPromptFiles(): Promise<
  ApiResponse<PromptFilesResponse>
> {
  const { data } = await apiClient.get<ApiResponse<PromptFilesResponse>>(
    "/system-prompt/files",
  );
  return data;
}

export async function getPromptFile(
  personaId: string,
  filename: string,
): Promise<ApiResponse<PromptFileContent>> {
  const { data } = await apiClient.get<ApiResponse<PromptFileContent>>(
    `/system-prompt/files/${encodeURIComponent(personaId)}/${encodeURIComponent(filename)}`,
  );
  return data;
}

export async function updatePromptFile(
  personaId: string,
  filename: string,
  content: string,
): Promise<ApiResponse<{ persona_id: string; filename: string; size: number }>> {
  const { data } = await apiClient.put<
    ApiResponse<{ persona_id: string; filename: string; size: number }>
  >(
    `/system-prompt/files/${encodeURIComponent(personaId)}/${encodeURIComponent(filename)}`,
    { content },
  );
  return data;
}

export async function previewSystemPrompt(
  personaId: string,
): Promise<ApiResponse<{ persona_id: string; prompt: string }>> {
  const { data } = await apiClient.get<
    ApiResponse<{ persona_id: string; prompt: string }>
  >(`/system-prompt/preview/${encodeURIComponent(personaId)}`);
  return data;
}
