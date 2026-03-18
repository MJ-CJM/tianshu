import type { CSSProperties, ReactNode } from "react";

interface MonoTextProps {
  children: ReactNode;
  style?: CSSProperties;
}

export default function MonoText({ children, style }: MonoTextProps) {
  return (
    <span
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
