import { Alert, Tag, Typography } from "antd";
import { Link } from "react-router-dom";

import { useT } from "../../i18n";

export type CapabilityMaturityLevel = "stableLimited" | "beta" | "experimental";

interface MaturityBadgeProps {
  maturity: CapabilityMaturityLevel;
}

interface CapabilityBoundaryProps extends MaturityBadgeProps {
  canDo: string;
  boundary: string;
}

interface CapabilitySummaryCardProps extends MaturityBadgeProps {
  title: string;
  status: string;
  description: string;
  to: string;
}

const MATURITY_COLOR: Record<CapabilityMaturityLevel, string> = {
  stableLimited: "green",
  beta: "blue",
  experimental: "gold",
};

const BOUNDARY_TYPE: Record<
  CapabilityMaturityLevel,
  "success" | "info" | "warning"
> = {
  stableLimited: "success",
  beta: "info",
  experimental: "warning",
};

export function MaturityBadge({ maturity }: MaturityBadgeProps) {
  const t = useT();
  return (
    <Tag color={MATURITY_COLOR[maturity]} style={{ marginInlineEnd: 0 }}>
      {t(`maturity.${maturity}`)}
    </Tag>
  );
}

export function CapabilityBoundary({
  maturity,
  canDo,
  boundary,
}: CapabilityBoundaryProps) {
  const t = useT();
  return (
    <Alert
      type={BOUNDARY_TYPE[maturity]}
      showIcon
      style={{ marginBottom: 16 }}
      message={<MaturityBadge maturity={maturity} />}
      description={
        <div style={{ display: "grid", gap: 6 }}>
          <div>
            <Typography.Text strong>{t("maturity.canDo")}：</Typography.Text>
            <Typography.Text>{canDo}</Typography.Text>
          </div>
          <div>
            <Typography.Text strong>
              {t("maturity.currentBoundary")}：
            </Typography.Text>
            <Typography.Text>{boundary}</Typography.Text>
          </div>
        </div>
      }
    />
  );
}

export function CapabilitySummaryCard({
  title,
  maturity,
  status,
  description,
  to,
}: CapabilitySummaryCardProps) {
  const t = useT();
  return (
    <article
      style={{
        border: "1px solid var(--ts-color-border)",
        borderRadius: 8,
        padding: 16,
        background: "var(--ts-color-surface)",
        display: "flex",
        minHeight: 168,
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <Typography.Title level={5} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
        <MaturityBadge maturity={maturity} />
      </div>
      <Tag style={{ alignSelf: "flex-start", marginInlineEnd: 0 }}>
        {status}
      </Tag>
      <Typography.Paragraph type="secondary" style={{ margin: 0, flex: 1 }}>
        {description}
      </Typography.Paragraph>
      <Link
        to={to}
        aria-label={`${t("page.controlCenter.viewCapability")} ${title}`}
      >
        {t("page.controlCenter.viewCapability")}
      </Link>
    </article>
  );
}
