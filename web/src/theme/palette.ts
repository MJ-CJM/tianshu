/**
 * 天枢调色板 · 单一色源(方案 A「朱批」)
 *
 * 设计宪法:墨为骨,朱为睛,纸为气。
 * - 界面 98% 由墨/纸/灰阶构成(Geist 式克制);
 * - 朱砂只出现在「与主上朱笔有关」的地方:待朱批、选中态朱杠、品牌印、focus 环;
 * - 状态色一律低饱和「器物色」,浅/深各一套,不共用。
 *
 * 此文件是唯一的颜色事实来源:theme/index.ts(AntD token)与
 * hooks/useTheme.ts(CSS 变量)都从这里取值,不得在组件里硬编码色值。
 */

export type ThemeMode = "light" | "dark";

export interface StatusColors {
  running: string;
  completed: string;
  failed: string;
  submitted: string;
  scheduled: string;
  planning: string;
  auditing: string;
  needs_review: string;
  cancelled: string;
}

export interface Palette {
  /** 页面底色(宣纸 / 烟墨) */
  bgPage: string;
  /** 容器底色(卡片、侧栏、页头) */
  bgContainer: string;
  /** 浮层底色(Modal / Popover / Dropdown) */
  bgElevated: string;
  /** 次级底色(表头、代码块衬底、选中衬底) */
  bgSubtle: string;
  /** 代码块底色 */
  bgCode: string;
  text: string;
  textSecondary: string;
  textTertiary: string;
  border: string;
  borderSecondary: string;
  borderHover: string;
  scrollbar: string;
  scrollbarHover: string;
  /** 朱砂:唯一的品牌强调色 */
  accent: string;
  accentHover: string;
  /** 朱砂淡染(selection / focus 底) */
  accentSoft: string;
  /** 朱砂实底上的文字色 */
  accentTextOn: string;
  /** 语义四色(低饱和器物色,同时喂给 AntD success/warning/error/info) */
  info: string;
  success: string;
  warning: string;
  error: string;
  status: StatusColors;
}

export const palettes: Record<ThemeMode, Palette> = {
  light: {
    bgPage: "#F7F5F1",
    bgContainer: "#FFFFFF",
    bgElevated: "#FFFFFF",
    bgSubtle: "#F4F2EC",
    bgCode: "#F4F2EC",
    text: "#1F1E1B",
    textSecondary: "#757167",
    textTertiary: "#8F8A7E",
    border: "#E5E1D8",
    borderSecondary: "#EDEAE2",
    borderHover: "#D5D0C4",
    scrollbar: "#D3CEC2",
    scrollbarHover: "#B8B2A4",
    accent: "#AE3F2C",
    accentHover: "#C14E39",
    accentSoft: "rgba(174, 63, 44, 0.10)",
    accentTextOn: "#FFFFFF",
    info: "#3D6C8E",
    success: "#45775A",
    warning: "#8A6B24",
    error: "#A5403D",
    status: {
      running: "#3D6C8E",
      completed: "#45775A",
      failed: "#A5403D",
      submitted: "#8A6B24",
      scheduled: "#8A6B24",
      planning: "#7A5E8A",
      auditing: "#3E7176",
      needs_review: "#AE3F2C",
      cancelled: "#7B776C",
    },
  },
  dark: {
    bgPage: "#161513",
    bgContainer: "#1E1C19",
    bgElevated: "#262420",
    bgSubtle: "#242220",
    bgCode: "#211F1B",
    text: "#EAE6DC",
    textSecondary: "#A29C8F",
    textTertiary: "#7E7A6F",
    border: "#33302A",
    borderSecondary: "#2A2823",
    borderHover: "#454138",
    scrollbar: "#4A463E",
    scrollbarHover: "#5C574D",
    accent: "#D96C52",
    accentHover: "#E5806A",
    accentSoft: "rgba(217, 108, 82, 0.16)",
    accentTextOn: "#1B120E",
    info: "#7FA7C4",
    success: "#82B091",
    warning: "#C9A85C",
    error: "#D08079",
    status: {
      running: "#7FA7C4",
      completed: "#82B091",
      failed: "#D08079",
      submitted: "#C9A85C",
      scheduled: "#C9A85C",
      planning: "#AD93BF",
      auditing: "#7CACAE",
      needs_review: "#D96C52",
      cancelled: "#8F8A80",
    },
  },
};

/**
 * AntD 预设调色板种子(blue / green / red / ...)的低饱和重调。
 * 覆盖种子后,全部 <Tag color="blue"> 之类的预设用法会整体换成器物色,
 * 深色模式由 darkAlgorithm 自动推导,无需逐处修改。
 */
export const presetSeeds = {
  blue: "#3D6C8E",
  green: "#45775A",
  red: "#A5403D",
  orange: "#9C6B2E",
  gold: "#8A6B24",
  yellow: "#8A6B24",
  purple: "#7A5E8A",
  cyan: "#3E7176",
  volcano: "#A8503A",
  magenta: "#96496B",
  pink: "#96496B",
  geekblue: "#44618E",
  lime: "#6B7A3A",
} as const;
