import apiClient from "./client";
import type { ApiResponse } from "./types";

export interface EstopState {
  kill_all: boolean;
  network_kill: boolean;
  frozen_tools: string[];
  updated_at: string | null;
  reason: string | null;
  engaged: boolean;
  available: boolean;
}

export async function getEstop(): Promise<ApiResponse<EstopState>> {
  const { data } = await apiClient.get<ApiResponse<EstopState>>("/estop");
  return data;
}

export interface EngagePayload {
  kill_all?: boolean;
  network_kill?: boolean;
  freeze_tools?: string[];
  reason?: string;
}

export async function engageEstop(
  payload: EngagePayload,
): Promise<ApiResponse<EstopState>> {
  const { data } = await apiClient.post<ApiResponse<EstopState>>(
    "/estop/engage",
    payload,
  );
  return data;
}

export interface ResumePayload {
  kill_all?: boolean;
  network_kill?: boolean;
  unfreeze_tools?: string[];
  all_clear?: boolean;
}

export async function resumeEstop(
  payload: ResumePayload,
): Promise<ApiResponse<EstopState>> {
  const { data } = await apiClient.post<ApiResponse<EstopState>>(
    "/estop/resume",
    payload,
  );
  return data;
}
