import { Layout, theme } from "antd";
import HealthDot from "../common/HealthDot";

export default function AppHeader() {
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
        <span
          style={{ color: token.colorTextSecondary, fontSize: 12, letterSpacing: 1, lineHeight: 1 }}
        >
          中枢台
        </span>
      </div>
      <HealthDot />
    </Layout.Header>
  );
}
