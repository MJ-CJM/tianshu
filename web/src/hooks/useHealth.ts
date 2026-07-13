import { useQuery } from "@tanstack/react-query";
import { getReadiness } from "../api/health";
import { POLL_INTERVAL_HEALTH } from "../utils/constants";

export function useHealth() {
  return useQuery({
    queryKey: ["health", "readiness"],
    queryFn: getReadiness,
    refetchInterval: POLL_INTERVAL_HEALTH,
    retry: 1,
  });
}
