import { Tooltip } from "antd";
import { useT } from "../../i18n";
import { FROZEN_CONNECTION_LABEL } from "../../contracts/frozenShell";

interface ConnectionIndicatorProps {
  isConnected: boolean;
}

export default function ConnectionIndicator({ isConnected }: ConnectionIndicatorProps) {
  const t = useT();
  const color = isConnected ? "var(--ts-color-success)" : "var(--ts-color-error)";
  const title = isConnected ? t("comp.connection.onlineTitle") : t("comp.connection.offlineTitle");

  return (
    <Tooltip title={title}>
      <span
        role="status"
        aria-label={title}
        style={{ display: "inline-flex", alignItems: "center", gap: 4, marginRight: 8 }}
      >
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
          {FROZEN_CONNECTION_LABEL}
        </span>
      </span>
    </Tooltip>
  );
}
