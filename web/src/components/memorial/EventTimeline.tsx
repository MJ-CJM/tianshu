import { useState } from "react";
import { Timeline, Typography, Collapse, theme, Tag, Space } from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  SendOutlined,
  ClockCircleOutlined,
  ToolOutlined,
  PlusCircleOutlined,
  StopOutlined,
  ExclamationCircleOutlined,
  ScheduleOutlined,
  BulbOutlined,
  SafetyCertificateOutlined,
  RedoOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  WarningOutlined,
  AuditOutlined,
  RollbackOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import type { EdictEvent } from "../../api/types";
import GlowCard from "../common/GlowCard";
import { formatTime } from "../../utils/format";
import { useT, type TFunction } from "../../i18n";
import type { ReactNode } from "react";

const EVENT_VISUALS: Record<string, { color: string; icon: ReactNode }> = {
  "edict.submitted": {
    color: "var(--ts-color-warning)",
    icon: <SendOutlined />,
  },
  "execution.started": {
    color: "var(--ts-color-info)",
    icon: <SyncOutlined spin />,
  },
  "execution.completed": {
    color: "var(--ts-color-success)",
    icon: <CheckCircleOutlined />,
  },
  "execution.failed": {
    color: "var(--ts-color-error)",
    icon: <CloseCircleOutlined />,
  },
  "execution.cancelled": {
    color: "var(--ts-status-cancelled)",
    icon: <CloseCircleOutlined />,
  },
  "iteration.started": {
    color: "var(--ts-color-info)",
    icon: <SyncOutlined spin />,
  },
  "tool.completed": { color: "var(--ts-color-info)", icon: <ToolOutlined /> },
  "tool.failed": {
    color: "var(--ts-color-error)",
    icon: <ExclamationCircleOutlined />,
  },
  "followup.submitted": {
    color: "var(--ts-status-planning)",
    icon: <PlusCircleOutlined />,
  },
  "edict.updated": { color: "var(--ts-color-warning)", icon: <SendOutlined /> },
  "edict.closed": { color: "var(--ts-color-success)", icon: <StopOutlined /> },
  "edict.scheduled": {
    color: "var(--ts-color-warning)",
    icon: <ScheduleOutlined />,
  },
  "plan.completed": {
    color: "var(--ts-status-planning)",
    icon: <BulbOutlined />,
  },
  "plan.pending_review": {
    color: "var(--ts-color-warning)",
    icon: <ExclamationCircleOutlined />,
  },
  "plan.approved": {
    color: "var(--ts-color-success)",
    icon: <CheckCircleOutlined />,
  },
  "plan.rejected": {
    color: "var(--ts-color-error)",
    icon: <CloseCircleOutlined />,
  },
  "audit.completed": {
    color: "var(--ts-status-auditing)",
    icon: <SafetyCertificateOutlined />,
  },
  "decree.approved": {
    color: "var(--ts-color-success)",
    icon: <CheckCircleOutlined />,
  },
  "decree.rejected": {
    color: "var(--ts-color-error)",
    icon: <CloseCircleOutlined />,
  },
  "decree.retry": { color: "var(--ts-color-warning)", icon: <RedoOutlined /> },
  "decree.cancelled": {
    color: "var(--ts-status-cancelled)",
    icon: <StopOutlined />,
  },
  "tool.blocked": { color: "var(--ts-color-error)", icon: <StopOutlined /> },
  "edict.audit.executed": {
    color: "var(--ts-status-auditing)",
    icon: <AuditOutlined />,
  },
  "edict.continuation.injected": {
    color: "var(--ts-status-planning)",
    icon: <RollbackOutlined />,
  },
  "edict.wind_down.entered": {
    color: "var(--ts-color-warning)",
    icon: <WarningOutlined />,
  },
  "edict.lifecycle.changed": {
    color: "var(--ts-color-info)",
    icon: <SwapOutlined />,
  },
  "outer_loop.paused": {
    color: "var(--ts-color-warning)",
    icon: <PauseCircleOutlined />,
  },
  "outer_loop.resumed": {
    color: "var(--ts-color-success)",
    icon: <PlayCircleOutlined />,
  },
};

function eventLabel(t: TFunction, eventType: string): string {
  return t(`event.label.${eventType}`);
}

/** Derive a status tag for a group of events */
function getGroupStatus(
  events: EdictEvent[],
  t: TFunction,
): { label: string; color: string } | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const et = events[i]!.event_type;
    if (et === "execution.completed")
      return { label: t("event.group.status.completed"), color: "success" };
    if (et === "execution.failed")
      return { label: t("event.group.status.failed"), color: "error" };
    if (et === "execution.cancelled")
      return { label: t("event.group.status.cancelled"), color: "default" };
  }
  const hasStarted = events.some((e) => e.event_type === "execution.started");
  if (hasStarted)
    return { label: t("event.group.status.running"), color: "processing" };
  return null;
}

/** Extract instruction text from a group's events */
function getGroupInstruction(events: EdictEvent[], t: TFunction): string {
  for (const e of events) {
    if (e.event_type === "followup.submitted") {
      const instr = e.payload?.instruction;
      if (typeof instr === "string") return instr;
    }
    if (e.event_type === "edict.submitted") {
      const goal = e.payload?.goal;
      if (typeof goal === "string") return goal;
    }
  }
  return t("event.group.unknownInstruction");
}

interface EventGroup {
  key: string;
  label: string;
  events: EdictEvent[];
  status: { label: string; color: string } | null;
}

/** Group events by memorial_id, preserving order. */
function groupEvents(events: EdictEvent[], t: TFunction): EventGroup[] {
  const groups: EventGroup[] = [];
  const memorialMap = new Map<string, EventGroup>();

  for (const event of events) {
    const mid = event.memorial_id;

    if (!mid) {
      if (groups.length > 0) {
        groups[groups.length - 1]!.events.push(event);
      } else {
        const fallback: EventGroup = {
          key: "__edict__",
          label: "",
          events: [event],
          status: null,
        };
        groups.push(fallback);
      }
      continue;
    }

    let group = memorialMap.get(mid);
    if (!group) {
      group = { key: mid, label: "", events: [], status: null };
      memorialMap.set(mid, group);
      groups.push(group);
    }
    group.events.push(event);
  }

  for (const group of groups) {
    const instruction = getGroupInstruction(group.events, t);
    const isFirst = group === groups[0];
    const prefix = isFirst
      ? t("event.group.label.initial")
      : t("event.group.label.followUp");
    const truncated =
      instruction.length > 40 ? instruction.slice(0, 40) + "..." : instruction;
    group.label =
      group.key === "__edict__"
        ? t("event.group.label.edictAction")
        : `${prefix}: ${truncated}`;
    group.status = getGroupStatus(group.events, t);
  }

  return groups;
}

function renderTimelineItem(
  event: EdictEvent,
  token: ReturnType<typeof theme.useToken>["token"],
  t: TFunction,
) {
  const visual = EVENT_VISUALS[event.event_type];
  const color = visual?.color ?? "var(--ts-status-cancelled)";
  const icon = visual?.icon ?? <ClockCircleOutlined />;
  const baseLabel = visual ? eventLabel(t, event.event_type) : event.event_type;

  const payload = event.payload ?? {};
  const toolName = payload.tool as string | undefined;
  const iteration = payload.iteration as number | undefined;

  let detail = baseLabel;
  if (
    (event.event_type === "tool.completed" ||
      event.event_type === "tool.failed") &&
    toolName
  ) {
    detail = `${baseLabel}: ${toolName}`;
    if (iteration !== undefined) {
      detail += ` (${t("event.detail.iteration")} ${iteration})`;
    }
  } else if (
    event.event_type === "iteration.started" &&
    iteration !== undefined
  ) {
    detail = `${baseLabel} #${iteration}`;
  } else if (event.event_type === "edict.wind_down.entered") {
    const field = payload.trigger_field as string | undefined;
    const ratio = payload.usage_ratio as number | undefined;
    const fieldLabel = field ? t(`event.field.${field}`) : "?";
    const pct =
      ratio != null
        ? ` (${t("event.detail.usagePct", { pct: Math.round(ratio * 100) })})`
        : "";
    detail = `${baseLabel}：${fieldLabel} ${t("event.detail.dimension")}${pct}`;
  } else if (event.event_type === "edict.lifecycle.changed") {
    const from = payload.from_phase as string | undefined;
    const to = payload.to_phase as string | undefined;
    const fromLabel = from ? t(`lifecycle.${from}`) : "?";
    const toLabel = to ? t(`lifecycle.${to}`) : "?";
    detail = `${baseLabel}：${fromLabel} → ${toLabel}`;
  } else if (event.event_type === "edict.audit.executed") {
    const passed = payload.passed as boolean | undefined;
    const gaps = payload.gaps_count as number | undefined;
    const exec = payload.executor_persona as string | undefined;
    const passLabel = passed
      ? t("event.detail.auditPassed")
      : t("event.detail.auditFailed", { n: gaps ?? 0 });
    const execLabel =
      exec === "actor_self_audit"
        ? t("event.detail.executorActorSelf")
        : t("event.detail.executorCritic");
    detail = `${baseLabel}：${passLabel}，${execLabel}`;
  } else if (
    event.event_type === "outer_loop.paused" ||
    event.event_type === "outer_loop.resumed"
  ) {
    detail =
      iteration !== undefined
        ? `${baseLabel} (${t("event.detail.iteration")} ${iteration})`
        : baseLabel;
  }

  return {
    key: event.id,
    color,
    dot: icon,
    children: (
      <div>
        <Typography.Text strong style={{ color: token.colorText }}>
          {detail}
        </Typography.Text>
        <br />
        <Typography.Text
          style={{ color: token.colorTextSecondary, fontSize: 12 }}
        >
          {formatTime(event.created_at)}
        </Typography.Text>
        {payload && Object.keys(payload).length > 0 && (
          <div
            style={{
              marginTop: 4,
              fontSize: 12,
              color: token.colorTextSecondary,
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            {Object.entries(payload)
              .filter(([k]) => !["type", "tool", "iteration"].includes(k))
              .map(([k, v]) => (
                <div key={k}>
                  {k}: {typeof v === "string" ? v : JSON.stringify(v)}
                </div>
              ))}
          </div>
        )}
      </div>
    ),
  };
}

interface EventTimelineProps {
  events: EdictEvent[];
}

export default function EventTimeline({ events }: EventTimelineProps) {
  const { token } = theme.useToken();
  const t = useT();

  // Hooks must be called before any conditional return
  const [activeKeys, setActiveKeys] = useState<string[]>(() => {
    if (events.length === 0) return [];
    // We need to derive the last group key — but groupEvents uses t which
    // depends on locale. Since the key is just memorial_id (or "__edict__"),
    // we can compute it directly from events without locale-specific labels.
    let lastKey: string | undefined;
    for (const e of events) {
      if (e.memorial_id) lastKey = e.memorial_id;
    }
    if (!lastKey && events.length > 0) lastKey = "__edict__";
    return lastKey ? [lastKey] : [];
  });
  const [expanded, setExpanded] = useState(false);

  if (events.length === 0) return null;

  const groups = groupEvents(events, t);

  const collapseItems = groups.map((group) => ({
    key: group.key,
    label: (
      <Space size={8}>
        <Typography.Text style={{ color: token.colorText }}>
          {group.label}
        </Typography.Text>
        {group.status && (
          <Tag
            color={group.status.color}
            bordered={false}
            style={{ marginRight: 0 }}
          >
            {group.status.label}
          </Tag>
        )}
        <Typography.Text
          style={{ color: token.colorTextTertiary, fontSize: 12 }}
        >
          {t("event.group.itemsCount", { n: group.events.length })}
        </Typography.Text>
      </Space>
    ),
    children: (
      <Timeline
        items={group.events.map((e) => renderTimelineItem(e, token, t))}
      />
    ),
  }));

  return (
    <GlowCard
      title={
        <span
          style={{ cursor: "pointer", userSelect: "none" }}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "▾" : "▸"} {t("event.timeline.title")} ({events.length})
        </span>
      }
    >
      {expanded && (
        <Collapse
          ghost
          activeKey={activeKeys}
          onChange={(keys) =>
            setActiveKeys(Array.isArray(keys) ? keys : [keys])
          }
          items={collapseItems}
        />
      )}
    </GlowCard>
  );
}
