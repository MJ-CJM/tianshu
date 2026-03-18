import { Layout } from "antd";
import HealthDot from "../common/HealthDot";

export default function AppHeader() {
  return (
    <Layout.Header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 24px",
        borderBottom: "1px solid rgba(30, 58, 95, 0.6)",
        height: 56,
        backdropFilter: "blur(12px)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <img src="/favicon.svg" alt="天枢" style={{ width: 28, height: 28, display: "block" }} />
        <span
          style={{
            color: "#00d4ff",
            fontFamily: "'Noto Serif SC', serif",
            fontWeight: 700,
            fontSize: 18,
            letterSpacing: 2,
            lineHeight: 1,
            textShadow: "0 0 12px rgba(0, 212, 255, 0.4)",
          }}
        >
          天枢
        </span>
        <span
          style={{ color: "#475569", fontSize: 12, letterSpacing: 1, lineHeight: 1 }}
        >
          中枢台
        </span>
      </div>
      <HealthDot />
    </Layout.Header>
  );
}
