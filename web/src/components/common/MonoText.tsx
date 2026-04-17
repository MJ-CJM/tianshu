import type { CSSProperties, ReactNode } from "react";

interface MonoTextProps {
  children: ReactNode;
  style?: CSSProperties;
  title?: string;
}

export default function MonoText({ children, style, title }: MonoTextProps) {
  return (
    <span
      title={title}
      style={{
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        fontSize: 13,
        ...style,
      }}
    >
      {children}
    </span>
  );
}
