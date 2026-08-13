import { useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createConsultation,
  getConsultation,
  listConsultations,
} from "../api/consultations";
import type { ConsultationRequest } from "../api/types";
import { useWebSocket } from "./useWebSocket";

const ACTIVE_STATUSES = new Set(["pending", "running"]);

export function useConsultation(id: string | null) {
  return useQuery({
    queryKey: ["consultation", id],
    queryFn: () => getConsultation(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.data?.status;
      // WS 推送是主路径，轮询留作兜底（WS 断线时仍能收敛）
      if (status && ACTIVE_STATUSES.has(status)) return 3000;
      return false;
    },
    select: (data) => data.data,
  });
}

export function useConsultations(limit = 20) {
  return useQuery({
    queryKey: ["consultations", limit],
    queryFn: () => listConsultations(limit),
    select: (data) => data.data ?? [],
  });
}

export function useCreateConsultation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ConsultationRequest) => createConsultation(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["consultations"] });
    },
  });
}

/** 订阅廷议 WS 事件：每位官员奏对到达即刷新，无需等下一次轮询。 */
export function useConsultationLiveUpdates(id: string | null) {
  const queryClient = useQueryClient();
  const { subscribe } = useWebSocket();

  useEffect(() => {
    return subscribe((msg) => {
      if (typeof msg.type !== "string" || !msg.type.startsWith("consultation.")) return;
      if (id && msg.consultation_id === id) {
        void queryClient.invalidateQueries({ queryKey: ["consultation", id] });
      }
      if (msg.type === "consultation.finished" || msg.type === "consultation.started") {
        void queryClient.invalidateQueries({ queryKey: ["consultations"] });
      }
    });
  }, [subscribe, queryClient, id]);
}
