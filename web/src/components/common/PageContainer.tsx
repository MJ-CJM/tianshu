import type { ReactNode } from "react";
import { Typography, theme } from "antd";

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
  const { token } = theme.useToken();

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
            color: token.colorText,
            fontFamily: "'Noto Serif SC', serif",
            fontWeight: 700,
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
