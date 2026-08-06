import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listDepartments,
  createDepartment,
  updateDepartment,
  deleteDepartment,
} from "../api/departments";
import type {
  DepartmentCreateRequest,
  DepartmentUpdateRequest,
} from "../api/types";

export function useDepartments() {
  return useQuery({
    queryKey: ["departments"],
    queryFn: listDepartments,
    select: (data) => data.data ?? [],
  });
}

export function useCreateDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DepartmentCreateRequest) => createDepartment(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["departments"] });
    },
  });
}

export function useUpdateDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DepartmentUpdateRequest }) =>
      updateDepartment(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["departments"] });
    },
  });
}

export function useDeleteDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteDepartment(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["departments"] });
    },
  });
}
