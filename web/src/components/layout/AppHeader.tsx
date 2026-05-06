import { Layout, theme } from "antd";
import HealthDot from "../common/HealthDot";
import ConnectionIndicator from "../common/ConnectionIndicator";
import LocaleSwitcher from "./LocaleSwitcher";
import { useT } from "../../i18n";

interface AppHeaderProps {
  isWsConnected?: boolean;
}

export default function AppHeader({ isWsConnected = false }: AppHeaderProps) {
  const t = useT();
  const { token } = theme.useToken();

  return (
    <Layout.Header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 24px",
        borderBottom: `1px solid ${token.colorBorder}`,
        height: 56,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <img src="/favicon.svg" alt="天枢" style={{ width: 28, height: 28, display: "block" }} />
        <span
          style={{
            color: token.colorText,
            fontFamily: "'Noto Serif SC', serif",
            fontWeight: 700,
            fontSize: 18,
            letterSpacing: 2,
            lineHeight: 1,
          }}
        >
          天枢
        </span>
      </div>
      <div
        style={{
          flex: 1,
          textAlign: "center",
          color: token.colorTextSecondary,
          fontSize: 13,
          letterSpacing: 1,
          fontStyle: "italic",
        }}
      >
        {t("comp.appHeader.tagline")}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <LocaleSwitcher />
        <ConnectionIndicator isConnected={isWsConnected} />
        <HealthDot />
      </div>
    </Layout.Header>
  );
}
