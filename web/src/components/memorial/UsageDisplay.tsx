import { Space, Typography, theme } from "antd";
import { ThunderboltOutlined } from "@ant-design/icons";
import type { UsageSummary } from "../../api/types";
import { formatTokens } from "../../utils/format";
import { useT } from "../../i18n";

interface UsageDisplayProps {
  usage: UsageSummary;
}

export default function UsageDisplay({ usage }: UsageDisplayProps) {
  const t = useT();
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
          {t("comp.usage.title")}
        </Typography.Text>
      </Space>
      <span style={{ color: token.colorBorder }}>|</span>
      <Space size={6}>
        <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
          {t("comp.usage.prompt")}
        </Typography.Text>
        <Typography.Text style={{ color: token.colorInfo, fontSize: 13, fontFamily: "'JetBrains Mono', monospace" }}>
          {formatTokens(usage.prompt_tokens)}
        </Typography.Text>
      </Space>
      <Space size={6}>
        <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
          {t("comp.usage.completion")}
        </Typography.Text>
        <Typography.Text style={{ color: token.colorInfo, fontSize: 13, fontFamily: "'JetBrains Mono', monospace" }}>
          {formatTokens(usage.completion_tokens)}
        </Typography.Text>
      </Space>
      <Space size={6}>
        <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
          {t("comp.usage.total")}
        </Typography.Text>
        <Typography.Text style={{ color: token.colorSuccess, fontSize: 13, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>
          {formatTokens(usage.total_tokens)}
        </Typography.Text>
      </Space>
      {typeof usage.cost_cny === "number" && usage.cost_cny > 0 && (
        <>
          <span style={{ color: token.colorBorder }}>|</span>
          <Space size={6}>
            <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
              {t("comp.usage.cost")}
            </Typography.Text>
            <Typography.Text
              style={{
                color: token.colorWarning,
                fontSize: 13,
                fontWeight: 600,
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              ¥{usage.cost_cny.toFixed(4)}
            </Typography.Text>
          </Space>
        </>
      )}
      {usage.actual_model && (
        <>
          <span style={{ color: token.colorBorder }}>|</span>
          <Space size={6}>
            <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
              {t("comp.usage.model")}
            </Typography.Text>
            <Typography.Text
              style={{
                color: token.colorTextSecondary,
                fontSize: 12,
                fontFamily: "'JetBrains Mono', monospace",
              }}
              title={
                usage.upstream_provider
                  ? `provider: ${usage.upstream_provider}`
                  : undefined
              }
            >
              {usage.actual_model}
            </Typography.Text>
          </Space>
        </>
      )}
    </div>
  );
}
