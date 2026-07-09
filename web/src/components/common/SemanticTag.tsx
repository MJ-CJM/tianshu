import { Tag, type TagProps } from "antd";

interface SemanticTagProps extends Omit<TagProps, "color"> {
  /** 语义色,须为 CSS 变量引用(如 "var(--ts-status-running)") */
  colorVar: string;
  /**
   * 实色模式:朱砂底 + 反白字。整个界面只允许「待朱批」使用,
   * 保证"全屏唯一实色 = 等你落笔"的语义成立。
   */
  solid?: boolean;
}

/**
 * 低饱和语义标签:淡染底 + 细描边 + 本色字(替代 AntD 实色 Tag)。
 * 颜色经 CSS 变量随主题切换,浅/深模式各有一套器物色。
 */
export default function SemanticTag({
  colorVar,
  solid = false,
  style,
  ...rest
}: SemanticTagProps) {
  const semanticStyle = solid
    ? {
        color: "var(--ts-color-accent-text-on)",
        background: colorVar,
        borderColor: colorVar,
        fontWeight: 600,
      }
    : {
        color: colorVar,
        background: `color-mix(in srgb, ${colorVar} 11%, transparent)`,
        borderColor: `color-mix(in srgb, ${colorVar} 30%, transparent)`,
      };

  return <Tag {...rest} style={{ ...semanticStyle, ...style }} />;
}
