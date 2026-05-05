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
import type { ReactNode } from "react";

const EVENT_CONFIG: Record<
  string,
  { color: string; icon: ReactNode; label: string }
> = {
  "edict.submitted": {
    color: "#faad14",
    icon: <SendOutlined />,
    label: "敕令颁发",
  },
  "execution.started": {
    color: "#1890ff",
    icon: <SyncOutlined spin />,
    label: "开始执行",
  },
  "execution.completed": {
    color: "#52c41a",
    icon: <CheckCircleOutlined />,
    label: "执行完成",
  },
  "execution.failed": {
    color: "#ff4d4f",
    icon: <CloseCircleOutlined />,
    label: "执行失败",
  },
  "execution.cancelled": {
    color: "#8c8c8c",
    icon: <CloseCircleOutlined />,
    label: "执行取消",
  },
  "iteration.started": {
    color: "#1890ff",
    icon: <SyncOutlined spin />,
    label: "迭代开始",
  },
  "tool.completed": {
    color: "#1890ff",
    icon: <ToolOutlined />,
    label: "工具调用",
  },
  "tool.failed": {
    color: "#ff4d4f",
    icon: <ExclamationCircleOutlined />,
    label: "工具失败",
  },
  "followup.submitted": {
    color: "#722ed1",
    icon: <PlusCircleOutlined />,
    label: "后续指令",
  },
  "edict.updated": {
    color: "#faad14",
    icon: <SendOutlined />,
    label: "敕令编辑",
  },
  "edict.closed": {
    color: "#52c41a",
    icon: <StopOutlined />,
    label: "敕令结案",
  },
  "edict.scheduled": {
    color: "#faad14",
    icon: <ScheduleOutlined />,
    label: "调度排期",
  },
  "plan.completed": {
    color: "#722ed1",
    icon: <BulbOutlined />,
    label: "规划完成",
  },
  "plan.pending_review": {
    color: "#faad14",
    icon: <ExclamationCircleOutlined />,
    label: "规划待审批",
  },
  "plan.approved": {
    color: "#52c41a",
    icon: <CheckCircleOutlined />,
    label: "规划已批准",
  },
  "plan.rejected": {
    color: "#ff4d4f",
    icon: <CloseCircleOutlined />,
    label: "规划已驳回",
  },
  "audit.completed": {
    color: "#13c2c2",
    icon: <SafetyCertificateOutlined />,
    label: "审计完成",
  },
  "decree.approved": {
    color: "#52c41a",
    icon: <CheckCircleOutlined />,
    label: "批红：准",
  },
  "decree.rejected": {
    color: "#ff4d4f",
    icon: <CloseCircleOutlined />,
    label: "批红：驳",
  },
  "decree.retry": {
    color: "#faad14",
    icon: <RedoOutlined />,
    label: "批红：重办",
  },
  "decree.cancelled": {
    color: "#8c8c8c",
    icon: <StopOutlined />,
    label: "批红：撤回",
  },
  "tool.blocked": {
    color: "#ff4d4f",
    icon: <StopOutlined />,
    label: "工具拦截",
  },
  // ↓ Phase 6 长任务相关事件
  "edict.audit.executed": {
    color: "#13c2c2",
    icon: <AuditOutlined />,
    label: "完成审计",
  },
  "edict.continuation.injected": {
    color: "#722ed1",
    icon: <RollbackOutlined />,
    label: "续转反哺",
  },
  "edict.wind_down.entered": {
    color: "#fa8c16",
    icon: <WarningOutlined />,
    label: "进入收尾",
  },
  "edict.lifecycle.changed": {
    color: "#1890ff",
    icon: <SwapOutlined />,
    label: "生命周期变更",
  },
  "outer_loop.paused": {
    color: "#faad14",
    icon: <PauseCircleOutlined />,
    label: "已暂停",
  },
  "outer_loop.resumed": {
    color: "#52c41a",
    icon: <PlayCircleOutlined />,
    label: "已恢复",
  },
};

const FIELD_LABEL_CN: Record<string, string> = {
  tokens: "Token",
  cost: "费用",
  time: "时间",
};

const PHASE_LABEL_CN: Record<string, string> = {
  active: "运行",
  paused: "暂停",
  winding_down: "收尾",
  complete: "终结",
};

/** Derive a status tag for a group of events */
function getGroupStatus(events: EdictEvent[]): {
  label: string;
  color: string;
} | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const t = events[i]!.event_type;
    if (t === "execution.completed") return { label: "完成", color: "success" };
    if (t === "execution.failed") return { label: "失败", color: "error" };
    if (t === "execution.cancelled") return { label: "取消", color: "default" };
  }
  // Still running if we have execution.started but no terminal event
  const hasStarted = events.some((e) => e.event_type === "execution.started");
  if (hasStarted) return { label: "执行中", color: "processing" };
  return null;
}

/** Extract instruction text from a group's events */
function getGroupInstruction(events: EdictEvent[]): string {
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
  return "未知指令";
}

interface EventGroup {
  key: string;
  label: string;
  events: EdictEvent[];
  status: { label: string; color: string } | null;
}

/** Group events by memorial_id, preserving order.
 *  Events without memorial_id are appended to the most recent group. */
function groupEvents(events: EdictEvent[]): EventGroup[] {
  const groups: EventGroup[] = [];
  const memorialMap = new Map<string, EventGroup>();

  for (const event of events) {
    const mid = event.memorial_id;

    if (!mid) {
      // Edict-level events (edict.updated, edict.closed) — append to the last group
      if (groups.length > 0) {
        groups[groups.length - 1]!.events.push(event);
      } else {
        // Edge case: edict-level event before any memorial group
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

  // Derive labels and statuses
  for (const group of groups) {
    const instruction = getGroupInstruction(group.events);
    const isFirst = group === groups[0];
    const prefix = isFirst ? "初始指令" : "后续指令";
    const truncated =
      instruction.length > 40 ? instruction.slice(0, 40) + "..." : instruction;
    group.label = group.key === "__edict__" ? "敕令操作" : `${prefix}: ${truncated}`;
    group.status = getGroupStatus(group.events);
  }

  return groups;
}

function renderTimelineItem(
  event: EdictEvent,
  token: ReturnType<typeof theme.useToken>["token"]
) {
  const config = EVENT_CONFIG[event.event_type] ?? {
    color: "#64748b",
    icon: <ClockCircleOutlined />,
    label: event.event_type,
  };

  const payload = event.payload ?? {};
  const toolName = payload.tool as string | undefined;
  const iteration = payload.iteration as number | undefined;

  let detail = config.label;
  if (
    (event.event_type === "tool.completed" ||
      event.event_type === "tool.failed") &&
    toolName
  ) {
    detail = `${config.label}: ${toolName}`;
    if (iteration !== undefined) {
      detail += ` (轮次 ${iteration})`;
    }
  } else if (
    event.event_type === "iteration.started" &&
    iteration !== undefined
  ) {
    detail = `${config.label} #${iteration}`;
  } else if (event.event_type === "edict.wind_down.entered") {
    const field = payload.trigger_field as string | undefined;
    const ratio = payload.usage_ratio as number | undefined;
    const fieldCN = field && FIELD_LABEL_CN[field] ? FIELD_LABEL_CN[field] : field ?? "?";
    const pct = ratio != null ? ` (已用 ${Math.round(ratio * 100)}%)` : "";
    detail = `${config.label}：${fieldCN} 维度${pct}`;
  } else if (event.event_type === "edict.lifecycle.changed") {
    const from = payload.from_phase as string | undefined;
    const to = payload.to_phase as string | undefined;
    const fromCN = from ? PHASE_LABEL_CN[from] ?? from : "?";
    const toCN = to ? PHASE_LABEL_CN[to] ?? to : "?";
    detail = `${config.label}：${fromCN} → ${toCN}`;
  } else if (event.event_type === "edict.audit.executed") {
    const passed = payload.passed as boolean | undefined;
    const gaps = payload.gaps_count as number | undefined;
    const exec = payload.executor_persona as string | undefined;
    const passLabel = passed ? "通过" : `未过（${gaps ?? 0} 项缺口）`;
    const execLabel = exec === "actor_self_audit" ? "actor 自审" : "critic";
    detail = `${config.label}：${passLabel}，由 ${execLabel}`;
  } else if (
    event.event_type === "outer_loop.paused" ||
    event.event_type === "outer_loop.resumed"
  ) {
    detail = iteration !== undefined ? `${config.label} (轮次 ${iteration})` : config.label;
  }

  return {
    key: event.id,
    color: config.color,
    dot: config.icon,
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

  if (events.length === 0) return null;

  const groups = groupEvents(events);

  // Default: expand only the last group
  const [activeKeys, setActiveKeys] = useState<string[]>(() => {
    if (groups.length === 0) return [];
    return [groups[groups.length - 1]!.key];
  });

  // Card-level collapse — default collapsed
  const [expanded, setExpanded] = useState(false);

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
          {group.events.length} 条
        </Typography.Text>
      </Space>
    ),
    children: (
      <Timeline
        items={group.events.map((e) => renderTimelineItem(e, token))}
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
          {expanded ? "▾" : "▸"} 办理记录 ({events.length})
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
