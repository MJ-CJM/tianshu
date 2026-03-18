import { theme } from "antd";
import type { ThemeConfig } from "antd";
import type { ThemeMode } from "../hooks/useTheme";

const sharedToken = {
  fontFamily:
    "'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  fontFamilyCode: "'JetBrains Mono', 'Fira Code', monospace",
  borderRadius: 10,
  wireframe: false,
};

const sharedComponents = {
  Descriptions: { titleMarginBottom: 8 },
  Statistic: { titleFontSize: 12, contentFontSize: 22 },
};

export const lightTheme: ThemeConfig = {
  token: {
    ...sharedToken,
    colorPrimary: "#1a1a1a",
    colorBgBase: "#f8f8f7",
    colorBgContainer: "#ffffff",
    colorBgElevated: "#ffffff",
    colorBorder: "#e8e8e5",
    colorText: "#1a1a1a",
    colorTextSecondary: "#8e8e8e",
  },
  components: {
    ...sharedComponents,
    Layout: {
      siderBg: "#ffffff",
      headerBg: "#ffffff",
      bodyBg: "transparent",
    },
    Menu: {
      itemBg: "transparent",
      itemSelectedBg: "rgba(0, 0, 0, 0.04)",
      itemHoverBg: "rgba(0, 0, 0, 0.02)",
    },
    Table: {
      headerBg: "#fafafa",
      rowHoverBg: "rgba(0, 0, 0, 0.02)",
    },
    Card: {
      colorBgContainer: "#ffffff",
      paddingLG: 20,
    },
    Button: {
      primaryShadow: "0 2px 4px rgba(0, 0, 0, 0.06)",
    },
  },
};

export const darkTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    ...sharedToken,
    colorPrimary: "#e8e8e8",
    colorBgBase: "#141414",
    colorBgContainer: "#1f1f1f",
    colorBgElevated: "#1f1f1f",
    colorBorder: "#303030",
    colorText: "#e8e8e8",
    colorTextSecondary: "#999999",
  },
  components: {
    ...sharedComponents,
    Layout: {
      siderBg: "#141414",
      headerBg: "#141414",
      bodyBg: "transparent",
    },
    Menu: {
      itemBg: "transparent",
      itemSelectedBg: "rgba(255, 255, 255, 0.08)",
      itemHoverBg: "rgba(255, 255, 255, 0.04)",
    },
    Table: {
      headerBg: "#1a1a1a",
      rowHoverBg: "rgba(255, 255, 255, 0.04)",
    },
    Card: {
      colorBgContainer: "#1f1f1f",
      paddingLG: 20,
    },
    Button: {
      primaryShadow: "0 2px 4px rgba(0, 0, 0, 0.2)",
    },
  },
};

export function getThemeConfig(mode: ThemeMode): ThemeConfig {
  return mode === "dark" ? darkTheme : lightTheme;
}
