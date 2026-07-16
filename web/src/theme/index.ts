import { theme } from "antd";
import type { ThemeConfig } from "antd";
import type { ThemeMode } from "../hooks/useTheme";
import { palettes, presetSeeds } from "./palette";

// 字体一律走本地：不引入 Google Fonts 等外部 CDN(离线 wheel/容器必须可用)。
// Noto/JetBrains 若本机已装则优先，否则降级到各平台的系统中文字体栈。
const sharedToken = {
  fontFamily:
    "'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', " +
    "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
  fontFamilyCode:
    "'JetBrains Mono', 'Fira Code', 'SF Mono', Menlo, Consolas, monospace",
  borderRadius: 8,
  borderRadiusLG: 12,
  borderRadiusSM: 6,
  wireframe: false,
  // AntD 预设调色板种子重调:全部 <Tag color="blue"> 等预设用法随之低饱和化
  ...presetSeeds,
};

const sharedComponents = {
  Descriptions: { titleMarginBottom: 8 },
  Statistic: { titleFontSize: 12, contentFontSize: 22 },
};

/** 在 base 底色上按 pct 混入 color(等价 color-mix),用于推导状态淡染底/描边。 */
function mix(color: string, base: string, pct: number): string {
  const c = parseInt(color.slice(1), 16);
  const b = parseInt(base.slice(1), 16);
  const ch = (shift: number) =>
    Math.round(((c >> shift) & 0xff) * pct + ((b >> shift) & 0xff) * (1 - pct));
  return `#${((ch(16) << 16) | (ch(8) << 8) | ch(0)).toString(16).padStart(6, "0")}`;
}

function buildTheme(mode: ThemeMode): ThemeConfig {
  const p = palettes[mode];
  const dark = mode === "dark";
  return {
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      ...sharedToken,
      // 主色 = 墨(浅)/ 米白(深):按钮保持无彩度,朱砂不做普通按钮色
      colorPrimary: p.text,
      colorBgBase: p.bgPage,
      colorBgLayout: p.bgPage,
      colorBgContainer: p.bgContainer,
      colorBgElevated: p.bgElevated,
      colorBorder: p.border,
      colorBorderSecondary: p.borderSecondary,
      colorText: p.text,
      colorTextSecondary: p.textSecondary,
      colorTextTertiary: p.textTertiary,
      // 链接:墨字,悬停见朱
      colorLink: p.text,
      colorLinkHover: p.accent,
      colorLinkActive: p.accentHover,
      // 语义四色接入器物色(Alert/message/Result/Badge 随之统一)
      colorInfo: p.info,
      colorSuccess: p.success,
      colorWarning: p.warning,
      colorError: p.error,
      colorPrimaryBorder: p.border,
      boxShadowSecondary:
        dark ? "none" : `0 8px 24px color-mix(in srgb, ${p.text} 8%, transparent)`,
      // 状态淡染底/描边与 SemanticTag 同一配方(11% / 30% 混入容器底),
      // 避免算法推导出浑浊底色
      colorInfoBg: mix(p.info, p.bgContainer, dark ? 0.16 : 0.11),
      colorInfoBorder: mix(p.info, p.bgContainer, dark ? 0.32 : 0.3),
      colorSuccessBg: mix(p.success, p.bgContainer, dark ? 0.16 : 0.11),
      colorSuccessBorder: mix(p.success, p.bgContainer, dark ? 0.32 : 0.3),
      colorWarningBg: mix(p.warning, p.bgContainer, dark ? 0.16 : 0.11),
      colorWarningBorder: mix(p.warning, p.bgContainer, dark ? 0.32 : 0.3),
      colorErrorBg: mix(p.error, p.bgContainer, dark ? 0.16 : 0.11),
      colorErrorBorder: mix(p.error, p.bgContainer, dark ? 0.32 : 0.3),
    },
    components: {
      ...sharedComponents,
      Layout: {
        siderBg: p.bgContainer,
        headerBg: p.bgContainer,
        bodyBg: "transparent",
      },
      Menu: {
        itemBg: "transparent",
        subMenuItemBg: "transparent",
        itemSelectedBg: p.bgSubtle,
        itemSelectedColor: p.text,
        itemHoverBg: dark ? "rgba(255, 255, 255, 0.04)" : "rgba(0, 0, 0, 0.03)",
        itemBorderRadius: 6,
        itemHeight: 36,
        groupTitleFontSize: 11,
      },
      Table: {
        headerBg: p.bgSubtle,
        headerSplitColor: "transparent",
        rowHoverBg: dark ? "rgba(255, 255, 255, 0.03)" : "rgba(31, 30, 27, 0.025)",
      },
      Card: {
        colorBgContainer: p.bgContainer,
        colorBorderSecondary: p.border,
        paddingLG: 20,
      },
      Button: {
        primaryShadow: dark ? "none" : "0 1px 2px rgba(31, 30, 27, 0.08)",
        defaultShadow: "none",
        dangerShadow: "none",
        fontWeight: 500,
        // 深色主色为米白,AntD 不会自动翻转实底按钮文字色,须显式给墨字
        primaryColor: dark ? "#1B1A17" : "#FFFFFF",
      },
      Tabs: {
        // 选中页签的墨线用朱砂——系统性的"朱笔"落点之一
        inkBarColor: p.accent,
        itemSelectedColor: p.text,
        itemHoverColor: p.text,
      },
      Tag: {
        defaultBg: p.bgSubtle,
        defaultColor: p.textSecondary,
      },
      Modal: {
        contentBg: p.bgElevated,
        headerBg: p.bgElevated,
      },
      Segmented: {
        trackBg: p.bgSubtle,
        itemSelectedBg: p.bgContainer,
      },
    },
  };
}

export const lightTheme: ThemeConfig = buildTheme("light");

export const darkTheme: ThemeConfig = buildTheme("dark");

export function getThemeConfig(mode: ThemeMode): ThemeConfig {
  return mode === "dark" ? darkTheme : lightTheme;
}
