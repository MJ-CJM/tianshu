import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listSchedulerJobs, cancelSchedulerJob } from "../api/scheduler";

export function useSchedulerJobs() {
  return useQuery({
    queryKey: ["scheduler_jobs"],
    queryFn: async () => {
      const res = await listSchedulerJobs();
      return res.data ?? [];
    },
    refetchInterval: 10_000,
  });
}

export function useCancelJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => cancelSchedulerJob(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scheduler_jobs"] });
    },
  });
}
