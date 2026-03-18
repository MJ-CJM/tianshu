import { Descriptions, Typography } from "antd";
import { SyncOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Memorial } from "../../api/types";
import GlowCard from "../common/GlowCard";
import StatusTag from "../edict/StatusTag";
import MonoText from "../common/MonoText";
import { formatTime, truncateId, formatDuration } from "../../utils/format";
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

  return (
    <GlowCard
      title={
        <span>
          {title}
          {isRunning && (
            <SyncOutlined spin style={{ marginLeft: 8, color: "#00d4ff" }} />
          )}
        </span>
      }
      className={isRunning ? glowStyles.runningGlow : undefined}
      style={{
        marginBottom: 24,
        borderLeft: `3px solid ${borderColor}`,
      }}
    >
      <Descriptions column={2} size="small" colon={false}>
        <Descriptions.Item label="编号">
          <MonoText style={{ fontSize: 12 }}>
            {truncateId(memorial.id)}
          </MonoText>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <StatusTag status={memorial.status} />
        </Descriptions.Item>
        {memorial.instruction && (
          <Descriptions.Item label="指令" span={2}>
            {memorial.instruction}
          </Descriptions.Item>
        )}
        <Descriptions.Item label="开折时间">
          {formatTime(memorial.started_at)}
        </Descriptions.Item>
        <Descriptions.Item label="封折时间">
          {formatTime(memorial.completed_at)}
        </Descriptions.Item>
        {memorial.started_at && memorial.completed_at && (
          <Descriptions.Item label="耗时">
            {formatDuration(memorial.started_at, memorial.completed_at)}
          </Descriptions.Item>
        )}
      </Descriptions>

      {memorial.summary && (
        <div style={{ marginTop: 16 }}>
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
        <div style={{ marginTop: 16 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            详文
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
        <div style={{ marginTop: 16 }}>
          <Typography.Text type="danger" style={{ fontSize: 12 }}>
            驳回
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
