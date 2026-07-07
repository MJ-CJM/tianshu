import type { EdictStatus, TaskStatus } from "../api/types";
import { useT } from "../i18n";

export const STATUS_COLORS: Record<TaskStatus, string> = {
  submitted: "#faad14",
  running: "#1890ff",
  completed: "#52c41a",
  failed: "#ff4d4f",
  cancelled: "#8c8c8c",
  scheduled: "#faad14",
  planning: "#722ed1",
  auditing: "#13c2c2",
  needs_review: "#fa8c16",
};

export const STATUS_LABELS: Record<TaskStatus, string> = {
  submitted: "已呈递",
  running: "办理中",
  completed: "已完成",
  failed: "已驳回",
  cancelled: "已撤回",
  scheduled: "已排期",
  planning: "规划中",
  auditing: "审计中",
  needs_review: "待朱批",
};

export const EDICT_STATUS_LABELS: Record<EdictStatus, string> = {
  open: "进行中",
  completed: "已结案",
  cancelled: "已撤回",
};

export const EDICT_STATUS_COLORS: Record<EdictStatus, string> = {
  open: "#1890ff",
  completed: "#52c41a",
  cancelled: "#8c8c8c",
};

/**
 * Locale-aware status labels. Use this hook in components that need to display
 * status labels in the user's currently selected locale (zh-classic / zh-modern / en).
 *
 * Static `STATUS_LABELS` constant above is preserved as a zh-classic fallback for
 * legacy call sites that cannot use hooks (e.g. non-component utilities).
 */
export function useStatusLabels(): Record<TaskStatus, string> {
  const t = useT();
  return {
    submitted: t("status.submitted"),
    running: t("status.running"),
    completed: t("status.completed"),
    failed: t("status.failed"),
    cancelled: t("status.cancelled"),
    scheduled: t("status.scheduled"),
    planning: t("status.planning"),
    auditing: t("status.auditing"),
    needs_review: t("status.needs_review"),
  };
}

export function useEdictStatusLabels(): Record<EdictStatus, string> {
  const t = useT();
  return {
    open: t("edictStatus.open"),
    completed: t("edictStatus.completed"),
    cancelled: t("edictStatus.cancelled"),
  };
}

export const PRIORITY_LABELS: Record<string, string> = {
  urgent: "紧急",
  normal: "普通",
  low: "低",
};

export const PRIORITY_COLORS: Record<string, string> = {
  urgent: "#ff4d4f",
  normal: "#1890ff",
  low: "#8c8c8c",
};

export const REVIEW_POLICY_LABELS: Record<string, string> = {
  never: "跳过复核",
  on_failure: "失败时复核",
  on_flag: "标记时复核",
  always: "始终复核",
};

export const SCHEDULE_TYPE_LABELS: Record<string, string> = {
  immediate: "即时",
  once: "定时",
  cron: "周期",
};

export const VERDICT_LABELS: Record<string, string> = {
  pass: "通过",
  flag: "标记",
  block: "阻止",
};

export const VERDICT_COLORS: Record<string, string> = {
  pass: "#52c41a",
  flag: "#faad14",
  block: "#ff4d4f",
};

export const REVIEW_STATUS_LABELS: Record<string, string> = {
  not_required: "无需复核",
  pending: "待复核",
  approved: "已批准",
  rejected: "已驳回",
};

export const POLL_INTERVAL_HEALTH = 10_000;
export const POLL_INTERVAL_DETAIL = 2_000;
export const PAGE_SIZE = 20;
