import { useQuery } from "@tanstack/react-query";
import { getEdict, getEdictMemorials, getEdictEvents } from "../api/edicts";
import { POLL_INTERVAL_DETAIL } from "../utils/constants";

export function useEdictDetail(edictId: string) {
  const edictQuery = useQuery({
    queryKey: ["edict", edictId],
    queryFn: () => getEdict(edictId),
    enabled: !!edictId,
    refetchInterval: ({ state }) => {
      const status = state.data?.data?.status;
      return status === "open" ? POLL_INTERVAL_DETAIL : false;
    },
  });

  const memorialsQuery = useQuery({
    queryKey: ["memorials", edictId],
    queryFn: () => getEdictMemorials(edictId),
    enabled: !!edictId,
    refetchInterval: ({ state }) => {
      const edict = edictQuery.data?.data;
      if (!edict || edict.status !== "open") return false;
      const memorials = state.data?.data;
      if (!memorials) return POLL_INTERVAL_DETAIL;
      const hasActive = memorials.some(
        (m) => m.status === "running" || m.status === "submitted",
      );
      return hasActive ? POLL_INTERVAL_DETAIL : false;
    },
  });

  const eventsQuery = useQuery({
    queryKey: ["events", edictId],
    queryFn: () => getEdictEvents(edictId),
    enabled: !!edictId,
    refetchInterval: () => {
      const memorials = memorialsQuery.data?.data;
      if (!memorials) return false;
      const hasActive = memorials.some(
        (m) => m.status === "running" || m.status === "submitted",
      );
      return hasActive ? POLL_INTERVAL_DETAIL : false;
    },
  });

  return {
    edict: edictQuery.data?.data ?? null,
    memorials: memorialsQuery.data?.data ?? [],
    events: eventsQuery.data?.data ?? [],
    isLoading:
      edictQuery.isLoading ||
      memorialsQuery.isLoading ||
      eventsQuery.isLoading,
    error: edictQuery.error ?? memorialsQuery.error ?? eventsQuery.error,
    refetch: () => {
      edictQuery.refetch();
      memorialsQuery.refetch();
      eventsQuery.refetch();
    },
  };
}
