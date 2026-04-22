import apiClient from "./client";
import type { Credential, CredentialCreate, CredentialUpdate } from "./types";

export async function listCredentials(): Promise<Credential[]> {
  const { data } = await apiClient.get<Credential[]>("/credentials");
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
  req: CredentialUpdate,
): Promise<Credential> {
  const { data } = await apiClient.patch<Credential>(
    `/credentials/${encodeURIComponent(id)}`,
    req,
  );
  return data;
}

export async function deleteCredential(id: string): Promise<void> {
  await apiClient.delete(`/credentials/${encodeURIComponent(id)}`);
}
