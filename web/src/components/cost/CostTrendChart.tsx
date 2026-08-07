import { Card, Empty } from "antd";
import type { CostRecord } from "../../api/types";
import { useT } from "../../i18n";

interface Props {
  records: CostRecord[];
  loading: boolean;
}

export default function CostTrendChart({ records, loading }: Props) {
  const t = useT();
  if (loading) {
    return <Card title={t("cost.trend.title")} loading />;
  }

  if (!records.length) {
    return (
      <Card title={t("cost.trend.title")}>
        <Empty description={t("cost.trend.empty")} />
      </Card>
    );
  }

  // Group by date
  const byDate: Record<string, { tokens: number; cost: number }> = {};
  for (const r of records) {
    const date = r.created_at.slice(0, 10);
    if (!byDate[date]) byDate[date] = { tokens: 0, cost: 0 };
    byDate[date].tokens += r.total_tokens;
    byDate[date].cost += r.cost_cny;
  }

  const dates = Object.keys(byDate).sort();

  return (
    <Card title={t("cost.trend.title")}>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: "8px", borderBottom: "1px solid var(--ts-color-border)" }}>{t("cost.trend.date")}</th>
              <th style={{ textAlign: "right", padding: "8px", borderBottom: "1px solid var(--ts-color-border)" }}>{t("cost.trend.tokens")}</th>
              <th style={{ textAlign: "right", padding: "8px", borderBottom: "1px solid var(--ts-color-border)" }}>{t("cost.trend.cost")}</th>
            </tr>
          </thead>
          <tbody>
            {dates.map((date) => (
              <tr key={date}>
                <td style={{ padding: "8px", borderBottom: "1px solid var(--ts-color-border)" }}>{date}</td>
                <td style={{ textAlign: "right", padding: "8px", borderBottom: "1px solid var(--ts-color-border)" }}>
                  {byDate[date]?.tokens.toLocaleString()}
                </td>
                <td style={{ textAlign: "right", padding: "8px", borderBottom: "1px solid var(--ts-color-border)" }}>
                  ¥{byDate[date]?.cost.toFixed(4)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
