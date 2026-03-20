import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listPersonas,
  getPersonaMetrics,
  createPersona,
  updatePersona,
  deletePersona,
} from "../api/personas";
import type { PersonaCreateRequest, PersonaUpdateRequest } from "../api/types";

export function usePersonas() {
  return useQuery({
    queryKey: ["personas"],
    queryFn: listPersonas,
    select: (data) => data.data ?? [],
  });
}

export function usePersonaMetrics(id: string | null) {
  return useQuery({
    queryKey: ["persona", "metrics", id],
    queryFn: () => getPersonaMetrics(id!),
    enabled: !!id,
    select: (data) => data.data,
  });
}

export function useCreatePersona() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PersonaCreateRequest) => createPersona(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["personas"] });
    },
  });
}

export function useUpdatePersona() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: PersonaUpdateRequest }) =>
      updatePersona(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["personas"] });
    },
  });
}

export function useDeletePersona() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deletePersona(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["personas"] });
    },
  });
}
