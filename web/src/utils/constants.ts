import type { EdictStatus, TaskStatus } from "../api/types";
import { useT } from "../i18n";

// 状态色一律引用 CSS 变量(hooks/useTheme.ts 注入),随浅/深主题切换。
// 展示请配合 SemanticTag(淡染)使用,不要塞给 <Tag color=>(会渲染成实色块)。
export const STATUS_COLORS: Record<TaskStatus, string> = {
  submitted: "var(--ts-status-submitted)",
  running: "var(--ts-status-running)",
  completed: "var(--ts-status-completed)",
  failed: "var(--ts-status-failed)",
  cancelled: "var(--ts-status-cancelled)",
  scheduled: "var(--ts-status-scheduled)",
  planning: "var(--ts-status-planning)",
  auditing: "var(--ts-status-auditing)",
  needs_review: "var(--ts-status-needs-review)",
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
  needs_review: "待裁决",
};

export const EDICT_STATUS_LABELS: Record<EdictStatus, string> = {
  open: "进行中",
  completed: "已结案",
  cancelled: "已撤回",
};

export const EDICT_STATUS_COLORS: Record<EdictStatus, string> = {
  open: "var(--ts-status-running)",
  completed: "var(--ts-status-completed)",
  cancelled: "var(--ts-status-cancelled)",
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
  urgent: "var(--ts-color-error)",
  normal: "var(--ts-color-info)",
  low: "var(--ts-status-cancelled)",
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
  pass: "var(--ts-color-success)",
  flag: "var(--ts-color-warning)",
  block: "var(--ts-color-error)",
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
