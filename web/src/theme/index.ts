import { theme, type ThemeConfig } from "antd";

const themeConfig: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#00d4ff",
    colorBgBase: "#0a0e1a",
    colorBgContainer: "rgba(17, 24, 39, 0.65)",
    colorBgElevated: "rgba(26, 35, 50, 0.85)",
    colorBorder: "rgba(30, 58, 95, 0.8)",
    colorText: "#e2e8f0",
    colorTextSecondary: "#94a3b8",
    fontFamily:
      "'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    fontFamilyCode: "'JetBrains Mono', 'Fira Code', monospace",
    borderRadius: 10,
    wireframe: false,
  },
  components: {
    Layout: {
      siderBg: "rgba(13, 19, 33, 0.85)",
      headerBg: "rgba(13, 19, 33, 0.9)",
      bodyBg: "transparent",
    },
    Menu: {
      darkItemBg: "transparent",
      darkItemSelectedBg: "rgba(0, 212, 255, 0.1)",
      darkItemHoverBg: "rgba(0, 212, 255, 0.06)",
    },
    Table: {
      headerBg: "rgba(17, 24, 39, 0.8)",
      rowHoverBg: "rgba(0, 212, 255, 0.05)",
    },
    Card: {
      colorBgContainer: "rgba(17, 24, 39, 0.65)",
      paddingLG: 20,
    },
    Button: {
      primaryShadow: "0 0 12px rgba(0, 212, 255, 0.3)",
    },
    Descriptions: {
      titleMarginBottom: 8,
    },
    Statistic: {
      titleFontSize: 12,
      contentFontSize: 22,
    },
  },
};

export default themeConfig;
