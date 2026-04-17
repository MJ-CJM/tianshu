import { Tooltip } from "antd";

interface ConnectionIndicatorProps {
  isConnected: boolean;
}

export default function ConnectionIndicator({ isConnected }: ConnectionIndicatorProps) {
  const color = isConnected ? "#52c41a" : "#ff4d4f";
  const label = isConnected ? "实时" : "离线";
  const title = isConnected ? "WebSocket 已连接" : "WebSocket 已断开，轮询降级中";

  return (
    <Tooltip title={title}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4, marginRight: 8 }}>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            backgroundColor: color,
            display: "inline-block",
          }}
        />
        <span style={{ fontSize: 12, color: "var(--ts-color-text-secondary)" }}>
          {label}
        </span>
      </span>
    </Tooltip>
  );
}
