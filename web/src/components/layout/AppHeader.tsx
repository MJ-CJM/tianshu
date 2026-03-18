import { Layout, Space, Typography } from "antd";
import HealthDot from "../common/HealthDot";

export default function AppHeader() {
  return (
    <Layout.Header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 24px",
        borderBottom: "1px solid #1e3a5f",
        height: 56,
      }}
    >
      <Space align="center" size={12}>
        <img src="/favicon.svg" alt="天枢" style={{ width: 28, height: 28 }} />
        <Typography.Title
          level={4}
          style={{
            margin: 0,
            color: "#00d4ff",
            fontFamily: "'Noto Serif SC', serif",
            fontWeight: 700,
            letterSpacing: 2,
          }}
        >
          天枢
        </Typography.Title>
        <Typography.Text
          style={{ color: "#64748b", fontSize: 12, marginLeft: 4 }}
        >
          中枢台
        </Typography.Text>
      </Space>
      <HealthDot />
    </Layout.Header>
  );
}
