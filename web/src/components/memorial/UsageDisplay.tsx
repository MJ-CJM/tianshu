import { Space, Typography } from "antd";
import { ThunderboltOutlined } from "@ant-design/icons";
import type { UsageSummary } from "../../api/types";
import { formatTokens } from "../../utils/format";

interface UsageDisplayProps {
  usage: UsageSummary;
}

export default function UsageDisplay({ usage }: UsageDisplayProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 24,
        padding: "10px 20px",
        marginBottom: 24,
        borderRadius: 10,
        background: "rgba(17, 24, 39, 0.5)",
        border: "1px solid rgba(30, 58, 95, 0.5)",
        backdropFilter: "blur(8px)",
      }}
    >
      <Space size={4} align="center">
        <ThunderboltOutlined style={{ color: "#00d4ff", fontSize: 13 }} />
        <Typography.Text style={{ color: "#64748b", fontSize: 12 }}>
          用墨统计
        </Typography.Text>
      </Space>
      <span style={{ color: "rgba(30, 58, 95, 0.8)" }}>|</span>
      <Space size={6}>
        <Typography.Text style={{ color: "#64748b", fontSize: 12 }}>
          进言
        </Typography.Text>
        <Typography.Text style={{ color: "#00d4ff", fontSize: 13, fontFamily: "'JetBrains Mono', monospace" }}>
          {formatTokens(usage.prompt_tokens)}
        </Typography.Text>
      </Space>
      <Space size={6}>
        <Typography.Text style={{ color: "#64748b", fontSize: 12 }}>
          奏报
        </Typography.Text>
        <Typography.Text style={{ color: "#00d4ff", fontSize: 13, fontFamily: "'JetBrains Mono', monospace" }}>
          {formatTokens(usage.completion_tokens)}
        </Typography.Text>
      </Space>
      <Space size={6}>
        <Typography.Text style={{ color: "#64748b", fontSize: 12 }}>
          合计
        </Typography.Text>
        <Typography.Text style={{ color: "#52c41a", fontSize: 13, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>
          {formatTokens(usage.total_tokens)}
        </Typography.Text>
      </Space>
    </div>
  );
}
