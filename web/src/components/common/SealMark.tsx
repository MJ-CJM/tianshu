interface SealMarkProps {
  /** 渲染尺寸(px) */
  size?: number;
  /** 印文,默认「枢」 */
  char?: string;
}

/**
 * 品牌印:朱砂印面 + 内细线印框 + 白文印文(印泥语法,白文恒为纸白)。
 * 印面/印文色走 --ts-color-seal-* 变量,深色模式印面自动加深一档。
 */
export default function SealMark({ size = 26, char = "枢" }: SealMarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden
      focusable="false"
      style={{ display: "block", flex: "none" }}
    >
      {/* 印面 */}
      <rect x="1" y="1" width="30" height="30" rx="5.5" fill="var(--ts-color-seal-bg)" />
      {/* 印框:内细线,印章的身份特征 */}
      <rect
        x="3.8"
        y="3.8"
        width="24.4"
        height="24.4"
        rx="3"
        fill="none"
        stroke="var(--ts-color-seal-glyph)"
        strokeOpacity="0.55"
        strokeWidth="1.2"
      />
      {/* 白文印文 */}
      <text
        x="16"
        y="16.4"
        textAnchor="middle"
        dominantBaseline="central"
        fontFamily="'Noto Serif SC', serif"
        fontWeight={700}
        fontSize="16.5"
        fill="var(--ts-color-seal-glyph)"
      >
        {char}
      </text>
    </svg>
  );
}
