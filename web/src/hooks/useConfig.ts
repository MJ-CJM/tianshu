import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getConfig, updateConfig } from "../api/config";
import type { LLMConfigUpdateRequest } from "../api/types";

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: getConfig,
  });
}

export function useUpdateConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (req: LLMConfigUpdateRequest) => updateConfig(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["config"] });
    },
  });
}
