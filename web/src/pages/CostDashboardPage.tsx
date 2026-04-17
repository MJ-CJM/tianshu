import { useState } from "react";
import { Typography, Segmented, Space } from "antd";
import CostSummaryCards from "../components/cost/CostSummaryCards";
import CostTrendChart from "../components/cost/CostTrendChart";
import CostRecordTable from "../components/cost/CostRecordTable";
import BudgetProgressBar from "../components/cost/BudgetProgressBar";
import { useCostSummary, useCostRecords, useCostBudget } from "../hooks/useCost";

const { Title } = Typography;

export default function CostDashboardPage() {
  const [period, setPeriod] = useState<string>("month");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const { data: summary, isLoading: summaryLoading } = useCostSummary(period);
  const { data: recordsData, isLoading: recordsLoading } = useCostRecords(
    undefined,
    pageSize,
    (page - 1) * pageSize,
  );
  const { data: budget, isLoading: budgetLoading } = useCostBudget();

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Title level={4} style={{ margin: 0 }}>
            户部账房
          </Title>
          <Segmented
            value={period}
            onChange={(v) => setPeriod(v as string)}
            options={[
              { label: "今日", value: "day" },
              { label: "本周", value: "week" },
              { label: "本月", value: "month" },
            ]}
          />
        </div>

        <CostSummaryCards summary={summary} loading={summaryLoading} />

        <BudgetProgressBar budget={budget} loading={budgetLoading} />

        <CostTrendChart
          records={recordsData?.records ?? []}
          loading={recordsLoading}
        />

        <CostRecordTable
          records={recordsData?.records ?? []}
          total={recordsData?.total ?? 0}
          loading={recordsLoading}
          page={page}
          pageSize={pageSize}
          onPageChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      </Space>
    </div>
  );
}
