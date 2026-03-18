import { theme, type ThemeConfig } from "antd";

const themeConfig: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#00d4ff",
    colorBgBase: "#0a0e1a",
    colorBgContainer: "#111827",
    colorBgElevated: "#1a2332",
    colorBorder: "#1e3a5f",
    colorText: "#e2e8f0",
    colorTextSecondary: "#94a3b8",
    fontFamily:
      "'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    fontFamilyCode: "'JetBrains Mono', 'Fira Code', monospace",
    borderRadius: 8,
    wireframe: false,
  },
  components: {
    Layout: {
      siderBg: "#0d1321",
      headerBg: "#0d1321",
      bodyBg: "#0a0e1a",
    },
    Menu: {
      darkItemBg: "#0d1321",
      darkItemSelectedBg: "rgba(0, 212, 255, 0.1)",
      darkItemHoverBg: "rgba(0, 212, 255, 0.06)",
    },
    Table: {
      headerBg: "#111827",
      rowHoverBg: "rgba(0, 212, 255, 0.05)",
    },
    Card: {
      colorBgContainer: "#111827",
    },
    Button: {
      primaryShadow: "0 0 10px rgba(0, 212, 255, 0.3)",
    },
  },
};

export default themeConfig;
