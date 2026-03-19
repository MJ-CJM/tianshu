import { useQuery } from "@tanstack/react-query";
import { listPersonas, getPersonaMetrics } from "../api/personas";

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
