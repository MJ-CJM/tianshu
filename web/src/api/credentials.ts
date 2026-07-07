import apiClient from "./client";
import type { Credential, CredentialCreate, CredentialUpdate } from "./types";

export async function listCredentials(
  kind?: "edict_auth" | "engine_provider",
): Promise<Credential[]> {
  const qs = kind ? `?kind=${kind}` : "";
  const { data } = await apiClient.get<Credential[]>(`/credentials${qs}`);
  return data;
}

export async function createCredential(
  req: CredentialCreate,
): Promise<Credential> {
  const { data } = await apiClient.post<Credential>("/credentials", req);
  return data;
}

export async function updateCredential(
  id: string,
  patch: CredentialUpdate,
): Promise<Credential> {
  const { data } = await apiClient.patch<Credential>(
    `/credentials/${encodeURIComponent(id)}`,
    patch,
  );
  return data;
}

export async function deleteCredential(id: string): Promise<void> {
  await apiClient.delete(`/credentials/${encodeURIComponent(id)}`);
}
