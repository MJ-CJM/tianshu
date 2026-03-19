import { useQuery, useMutation } from "@tanstack/react-query";
import { createConsultation, getConsultation } from "../api/consultations";
import type { ConsultationRequest } from "../api/types";

export function useConsultation(id: string | null) {
  return useQuery({
    queryKey: ["consultation", id],
    queryFn: () => getConsultation(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.data?.status;
      if (status === "pending" || status === "running") return 2000;
      return false;
    },
    select: (data) => data.data,
  });
}

export function useCreateConsultation() {
  return useMutation({
    mutationFn: (body: ConsultationRequest) => createConsultation(body),
    onSuccess: (data) => data,
  });
}
