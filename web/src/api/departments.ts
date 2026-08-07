import apiClient from "./client";
import type {
  ApiResponse,
  DepartmentInfo,
  DepartmentCreateRequest,
  DepartmentUpdateRequest,
} from "./types";

export async function listDepartments(): Promise<ApiResponse<DepartmentInfo[]>> {
  const { data } = await apiClient.get<ApiResponse<DepartmentInfo[]>>("/departments");
  return data;
}

export async function createDepartment(
  body: DepartmentCreateRequest,
): Promise<ApiResponse<DepartmentInfo>> {
  const { data } = await apiClient.post<ApiResponse<DepartmentInfo>>(
    "/departments",
    body,
  );
  return data;
}

export async function updateDepartment(
  id: string,
  body: DepartmentUpdateRequest,
): Promise<ApiResponse<DepartmentInfo>> {
  const { data } = await apiClient.put<ApiResponse<DepartmentInfo>>(
    `/departments/${id}`,
    body,
  );
  return data;
}

export async function deleteDepartment(
  id: string,
): Promise<ApiResponse<{ id: string }>> {
  const { data } = await apiClient.delete<ApiResponse<{ id: string }>>(
    `/departments/${id}`,
  );
  return data;
}
