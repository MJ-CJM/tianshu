import apiClient from "./client";
import type { ApiResponse, ConsultationRequest, ConsultationResponse } from "./types";

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
