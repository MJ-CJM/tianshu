import { useState } from "react";
import { Alert, Typography, Segmented, Space } from "antd";
import CostSummaryCards from "../components/cost/CostSummaryCards";
import CostTrendChart from "../components/cost/CostTrendChart";
import CostRecordTable from "../components/cost/CostRecordTable";
import BudgetProgressBar from "../components/cost/BudgetProgressBar";
import ProviderPricingCard from "../components/cost/ProviderPricingCard";
import { useCostSummary, useCostRecords, useCostBudget } from "../hooks/useCost";
import { useT } from "../i18n";
import PageQueryError from "../components/states/PageQueryError";

const { Title } = Typography;

export default function CostDashboardPage() {
  const t = useT();
  const [period, setPeriod] = useState<string>("month");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const summaryQuery = useCostSummary(period);
  const recordsQuery = useCostRecords(
    undefined,
    pageSize,
    (page - 1) * pageSize,
  );
  const budgetQuery = useCostBudget();
  const queryError = summaryQuery.error ?? recordsQuery.error ?? budgetQuery.error;

  if (queryError) {
    return (
      <div style={{ padding: 24 }}>
        <PageQueryError
          error={queryError}
          onRetry={() => {
            void summaryQuery.refetch();
            void recordsQuery.refetch();
            void budgetQuery.refetch();
          }}
        />
      </div>
    );
  }

  const summary = summaryQuery.data;
  const recordsData = recordsQuery.data;
  const budget = budgetQuery.data;

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

        <Alert type="info" showIcon message={t("cost.trackingNote")} />

        <CostSummaryCards summary={summary} loading={summaryQuery.isLoading} />

        <BudgetProgressBar budget={budget} loading={budgetQuery.isLoading} />

        <ProviderPricingCard />

        <CostTrendChart
          records={recordsData?.records ?? []}
          loading={recordsQuery.isLoading}
        />

        <CostRecordTable
          records={recordsData?.records ?? []}
          total={recordsData?.total ?? 0}
          loading={recordsQuery.isLoading}
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
