import { Timeline, Typography, Collapse } from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  SendOutlined,
  ClockCircleOutlined,
  ToolOutlined,
  PlusCircleOutlined,
  StopOutlined,
} from "@ant-design/icons";
import type { EdictEvent } from "../../api/types";
import GlowCard from "../common/GlowCard";
import { formatTime } from "../../utils/format";
import type { ReactNode } from "react";

const EVENT_CONFIG: Record<string, { color: string; icon: ReactNode; label: string }> = {
  "edict.submitted": { color: "#faad14", icon: <SendOutlined />, label: "敕令颁发" },
  "execution.started": { color: "#00d4ff", icon: <SyncOutlined spin />, label: "开始执行" },
  "execution.completed": { color: "#52c41a", icon: <CheckCircleOutlined />, label: "执行完成" },
  "execution.failed": { color: "#ff4d4f", icon: <CloseCircleOutlined />, label: "执行失败" },
  "execution.cancelled": { color: "#8c8c8c", icon: <CloseCircleOutlined />, label: "执行取消" },
  "iteration.started": { color: "#1890ff", icon: <SyncOutlined spin />, label: "迭代开始" },
  "tool.completed": { color: "#13c2c2", icon: <ToolOutlined />, label: "工具调用" },
  "followup.submitted": { color: "#722ed1", icon: <PlusCircleOutlined />, label: "后续指令" },
  "edict.closed": { color: "#52c41a", icon: <StopOutlined />, label: "敕令结案" },
};

interface EventTimelineProps {
  events: EdictEvent[];
}

export default function EventTimeline({ events }: EventTimelineProps) {
  if (events.length === 0) return null;

  const items = events.map((event) => {
    const config = EVENT_CONFIG[event.event_type] ?? {
      color: "#64748b",
      icon: <ClockCircleOutlined />,
      label: event.event_type,
    };

    const payload = event.payload ?? {};
    const toolName = payload.tool as string | undefined;
    const iteration = payload.iteration as number | undefined;

    let detail = config.label;
    if (event.event_type === "tool.completed" && toolName) {
      detail = `${config.label}: ${toolName}`;
      if (iteration !== undefined) {
        detail += ` (轮次 ${iteration})`;
      }
    } else if (event.event_type === "iteration.started" && iteration !== undefined) {
      detail = `${config.label} #${iteration}`;
    }

    return {
      key: event.id,
      color: config.color,
      dot: config.icon,
      children: (
        <div>
          <Typography.Text strong style={{ color: "#e2e8f0" }}>
            {detail}
          </Typography.Text>
          <br />
          <Typography.Text
            style={{ color: "#64748b", fontSize: 12 }}
          >
            {formatTime(event.created_at)}
          </Typography.Text>
          {payload && Object.keys(payload).length > 0 && (
            <div
              style={{
                marginTop: 4,
                fontSize: 12,
                color: "#94a3b8",
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
  });

  return (
    <GlowCard title={`办理记录 (${events.length})`}>
      <Collapse
        ghost
        items={[
          {
            key: "timeline",
            label: (
              <Typography.Text style={{ color: "#94a3b8" }}>
                展开查看 {events.length} 条记录
              </Typography.Text>
            ),
            children: <Timeline items={items} />,
          },
        ]}
      />
    </GlowCard>
  );
}
