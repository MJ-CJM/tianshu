import { Space, Typography } from "antd";
import { SyncOutlined, ClockCircleOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Memorial } from "../../api/types";
import GlowCard from "../common/GlowCard";
import StatusTag from "../edict/StatusTag";
import { formatDuration } from "../../utils/format";
import { STATUS_COLORS } from "../../utils/constants";
import glowStyles from "../common/GlowCard.module.css";

interface MemorialCardProps {
  memorial: Memorial;
  index?: number;
}

export default function MemorialCard({ memorial, index }: MemorialCardProps) {
  const title = index !== undefined ? `奏折 #${index + 1}` : "奏折";
  const isRunning = memorial.status === "running";
  const borderColor = STATUS_COLORS[memorial.status] ?? "#1e3a5f";
  const duration = formatDuration(memorial.started_at, memorial.completed_at);
  const hasDuration = duration !== "—";

  // When summary equals result, only show result (avoid duplication)
  const showSummary = memorial.summary && memorial.summary !== memorial.result;

  return (
    <GlowCard
      title={
        <Space size="middle">
          <span>{title}</span>
          <StatusTag status={memorial.status} />
          {isRunning && <SyncOutlined spin style={{ color: "#00d4ff" }} />}
          {hasDuration && (
            <Typography.Text style={{ color: "#64748b", fontSize: 12 }}>
              <ClockCircleOutlined style={{ marginRight: 4 }} />
              {duration}
            </Typography.Text>
          )}
        </Space>
      }
      className={isRunning ? glowStyles.runningGlow : undefined}
      style={{
        marginBottom: 24,
        borderLeft: `3px solid ${borderColor}`,
      }}
    >
      {memorial.instruction && (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text style={{ color: "#94a3b8", fontSize: 12 }}>
            指令：
          </Typography.Text>
          <Typography.Text style={{ color: "#e2e8f0" }}>
            {memorial.instruction}
          </Typography.Text>
        </div>
      )}

      {showSummary && (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            要旨
          </Typography.Text>
          <Typography.Paragraph
            style={{ color: "#e2e8f0", marginTop: 4, marginBottom: 0 }}
          >
            {memorial.summary}
          </Typography.Paragraph>
        </div>
      )}

      {memorial.result && (
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {showSummary ? "详文" : "奏报"}
          </Typography.Text>
          <div
            className="memorial-markdown"
            style={{
              color: "#e2e8f0",
              marginTop: 4,
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 13,
              background: "rgba(0,0,0,0.3)",
              padding: 12,
              borderRadius: 6,
              border: "1px solid #1e3a5f",
              lineHeight: 1.7,
            }}
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {memorial.result}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {memorial.error && (
        <div style={{ marginTop: 12 }}>
          <Typography.Text type="danger" style={{ fontSize: 12 }}>
            未竟
          </Typography.Text>
          <Typography.Paragraph
            style={{
              color: "#ff4d4f",
              marginTop: 4,
              marginBottom: 0,
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 13,
            }}
          >
            {memorial.error}
          </Typography.Paragraph>
        </div>
      )}
    </GlowCard>
  );
}
