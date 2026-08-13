import apiClient from "./client";
import type {
  ApiResponse,
  ConsultationRequest,
  ConsultationResponse,
  RoundRequest,
} from "./types";

export async function createConsultation(
  body: ConsultationRequest,
): Promise<ApiResponse<ConsultationResponse>> {
  const { data } = await apiClient.post<ApiResponse<ConsultationResponse>>(
    "/consultations",
    body,
  );
  return data;
}

export async function getConsultation(
  id: string,
): Promise<ApiResponse<ConsultationResponse>> {
  const { data } = await apiClient.get<ApiResponse<ConsultationResponse>>(
    `/consultations/${id}`,
  );
  return data;
}

export async function listConsultations(
  limit = 20,
): Promise<ApiResponse<ConsultationResponse[]>> {
  const { data } = await apiClient.get<ApiResponse<ConsultationResponse[]>>(
    "/consultations",
    { params: { limit } },
  );
  return data;
}

export async function appendConsultationRound(
  id: string,
  body: RoundRequest,
): Promise<ApiResponse<{ id: string; round_index: number; status: string }>> {
  const { data } = await apiClient.post<
    ApiResponse<{ id: string; round_index: number; status: string }>
  >(`/consultations/${id}/rounds`, body);
  return data;
}

export async function setConsultationVerdict(
  id: string,
  verdict: string,
): Promise<ApiResponse<ConsultationResponse>> {
  const { data } = await apiClient.put<ApiResponse<ConsultationResponse>>(
    `/consultations/${id}/verdict`,
    { verdict },
  );
  return data;
}
