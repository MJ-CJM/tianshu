import type { EdictStatus, TaskStatus } from "../api/types";

export const STATUS_COLORS: Record<TaskStatus, string> = {
  submitted: "#faad14",
  running: "#00d4ff",
  completed: "#52c41a",
  failed: "#ff4d4f",
  cancelled: "#8c8c8c",
};

export const STATUS_LABELS: Record<TaskStatus, string> = {
  submitted: "已呈递",
  running: "办理中",
  completed: "已完成",
  failed: "已驳回",
  cancelled: "已撤回",
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

export const POLL_INTERVAL_HEALTH = 10_000;
export const POLL_INTERVAL_DETAIL = 2_000;
export const PAGE_SIZE = 20;
