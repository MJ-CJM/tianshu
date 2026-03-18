import type { ReactNode } from "react";
import { Typography } from "antd";

interface PageContainerProps {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
}

export default function PageContainer({
  title,
  extra,
  children,
}: PageContainerProps) {
  return (
    <div style={{ padding: "24px 32px" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 24,
        }}
      >
        <Typography.Title
          level={3}
          style={{
            margin: 0,
            color: "#e2e8f0",
            fontFamily: "'Noto Serif SC', serif",
            fontWeight: 700,
            textShadow: "0 0 20px rgba(0, 212, 255, 0.15)",
          }}
        >
          {title}
        </Typography.Title>
        {extra}
      </div>
      {children}
    </div>
  );
}
