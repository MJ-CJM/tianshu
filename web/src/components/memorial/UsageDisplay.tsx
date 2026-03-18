import { Space, Typography, theme } from "antd";
import { ThunderboltOutlined } from "@ant-design/icons";
import type { UsageSummary } from "../../api/types";
import { formatTokens } from "../../utils/format";

interface UsageDisplayProps {
  usage: UsageSummary;
}

export default function UsageDisplay({ usage }: UsageDisplayProps) {
  const { token } = theme.useToken();

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
        background: "var(--ts-color-bg-subtle)",
        border: `1px solid ${token.colorBorder}`,
      }}
    >
      <Space size={4} align="center">
        <ThunderboltOutlined style={{ color: token.colorInfo, fontSize: 13 }} />
        <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
          用墨统计
        </Typography.Text>
      </Space>
      <span style={{ color: token.colorBorder }}>|</span>
      <Space size={6}>
        <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
          进言
        </Typography.Text>
        <Typography.Text style={{ color: token.colorInfo, fontSize: 13, fontFamily: "'JetBrains Mono', monospace" }}>
          {formatTokens(usage.prompt_tokens)}
        </Typography.Text>
      </Space>
      <Space size={6}>
        <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
          奏报
        </Typography.Text>
        <Typography.Text style={{ color: token.colorInfo, fontSize: 13, fontFamily: "'JetBrains Mono', monospace" }}>
          {formatTokens(usage.completion_tokens)}
        </Typography.Text>
      </Space>
      <Space size={6}>
        <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
          合计
        </Typography.Text>
        <Typography.Text style={{ color: token.colorSuccess, fontSize: 13, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>
          {formatTokens(usage.total_tokens)}
        </Typography.Text>
      </Space>
    </div>
  );
}
