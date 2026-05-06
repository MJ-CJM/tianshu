import { useState } from "react";
import { Typography, Segmented, Space } from "antd";
import CostSummaryCards from "../components/cost/CostSummaryCards";
import CostTrendChart from "../components/cost/CostTrendChart";
import CostRecordTable from "../components/cost/CostRecordTable";
import BudgetProgressBar from "../components/cost/BudgetProgressBar";
import ProviderPricingCard from "../components/cost/ProviderPricingCard";
import { useCostSummary, useCostRecords, useCostBudget } from "../hooks/useCost";
import { useT } from "../i18n";

const { Title } = Typography;

export default function CostDashboardPage() {
  const t = useT();
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
            {t("cost.title")}
          </Title>
          <Segmented
            value={period}
            onChange={(v) => setPeriod(v as string)}
            options={[
              { label: t("cost.period.day"), value: "day" },
              { label: t("cost.period.week"), value: "week" },
              { label: t("cost.period.month"), value: "month" },
            ]}
          />
        </div>

        <CostSummaryCards summary={summary} loading={summaryLoading} />

        <BudgetProgressBar budget={budget} loading={budgetLoading} />

        <ProviderPricingCard />

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
