import { useQuery } from "@tanstack/react-query";
import { getAuditStats } from "../api/audit";

export function useAuditStats() {
  return useQuery({
    queryKey: ["audit_stats"],
    queryFn: async () => {
      const res = await getAuditStats();
      return res.data;
    },
    refetchInterval: 30_000,
  });
}
