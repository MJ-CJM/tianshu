import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  cancelSchedulerJob,
  listSchedulerJobs,
  pauseSchedulerJob,
  resumeSchedulerJob,
  runSchedulerJobNow,
  updateSchedulerJob,
} from "../api/scheduler";
import type { EdictSchedule } from "../api/types";

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

function useJobMutation<T>(mutationFn: (value: T) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scheduler_jobs"] });
    },
  });
}

export function usePauseJob() {
  return useJobMutation((jobId: string) => pauseSchedulerJob(jobId));
}

export function useResumeJob() {
  return useJobMutation((jobId: string) => resumeSchedulerJob(jobId));
}

export function useRunJobNow() {
  return useJobMutation((jobId: string) => runSchedulerJobNow(jobId));
}

export function useUpdateJob() {
  return useJobMutation(
    ({ jobId, schedule }: { jobId: string; schedule: EdictSchedule }) =>
      updateSchedulerJob(jobId, schedule),
  );
}
