import type { ReactNode } from "react";
import { Typography, theme } from "antd";

interface PageContainerProps {
  title: string;
  /** 标题旁的文字徽标；与标题保持为独立节点，避免改变 heading 的可访问名称。 */
  titleBadge?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  /** 传入则内容(标题+主体)限宽并水平居中——表单类页面用,避免超宽屏内容孤零零贴左。 */
  contentMaxWidth?: number;
}

export default function PageContainer({
  title,
  titleBadge,
  extra,
  children,
  contentMaxWidth,
}: PageContainerProps) {
  const { token } = theme.useToken();

  return (
    <div style={{ padding: "24px 32px" }}>
      <div
        style={{
          maxWidth: contentMaxWidth,
          margin: contentMaxWidth ? "0 auto" : undefined,
        }}
      >
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 12,
            marginBottom: 24,
          }}
        >
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: 8,
            }}
          >
            <Typography.Title
              level={3}
              style={{
                margin: 0,
                color: token.colorText,
                fontFamily: "'Noto Serif SC', serif",
                fontWeight: 700,
                letterSpacing: "0.04em",
              }}
            >
              {title}
            </Typography.Title>
            {titleBadge}
          </div>
          {extra}
        </div>
        {children}
      </div>
    </div>
  );
}
