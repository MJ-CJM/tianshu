import { Card, Empty } from "antd";
import type { CostRecord } from "../../api/types";

interface Props {
  records: CostRecord[];
  loading: boolean;
}

export default function CostTrendChart({ records, loading }: Props) {
  if (loading) {
    return <Card title="成本趋势" loading />;
  }

  if (!records.length) {
    return (
      <Card title="成本趋势">
        <Empty description="暂无成本记录" />
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
    <Card title="成本趋势">
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: "8px", borderBottom: "1px solid #f0f0f0" }}>日期</th>
              <th style={{ textAlign: "right", padding: "8px", borderBottom: "1px solid #f0f0f0" }}>Token</th>
              <th style={{ textAlign: "right", padding: "8px", borderBottom: "1px solid #f0f0f0" }}>成本 (CNY)</th>
            </tr>
          </thead>
          <tbody>
            {dates.map((date) => (
              <tr key={date}>
                <td style={{ padding: "8px", borderBottom: "1px solid #f0f0f0" }}>{date}</td>
                <td style={{ textAlign: "right", padding: "8px", borderBottom: "1px solid #f0f0f0" }}>
                  {byDate[date]?.tokens.toLocaleString()}
                </td>
                <td style={{ textAlign: "right", padding: "8px", borderBottom: "1px solid #f0f0f0" }}>
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
