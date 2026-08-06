import { useState } from "react";
import {
  Button,
  Card,
  Col,
  Empty,
  Progress,
  Row,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import { ExperimentOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { getEvalRun, listEvalRuns, listEvalSets } from "../api/evals";
import type { EvalGoalResult, EvalRunBrief, EvalSet } from "../api/types";
import {
  CapabilityBoundary,
  MaturityBadge,
} from "../components/capabilities/CapabilityMaturity";
import PageContainer from "../components/common/PageContainer";
import MonoText from "../components/common/MonoText";
import { formatTime } from "../utils/format";
import { useT } from "../i18n";
import { toApiProblem } from "../api/client";
import PageDataState from "../components/states/PageDataState";
import { problemPageStatus } from "../components/states/problemPageStatus";

function DeltaTag({ delta }: { delta: number | null }) {
  const t = useT();
  if (delta === null || delta === undefined)
    return <Tag>{t("evals.firstRun")}</Tag>;
  const color = delta > 0 ? "success" : delta < 0 ? "error" : "default";
  const sign = delta > 0 ? "+" : "";
  return <Tag color={color}>{`${sign}${delta.toFixed(4)}`}</Tag>;
}

function RunDetail({ runId }: { runId: string }) {
  const t = useT();
  const runQuery = useQuery({
    queryKey: ["evals", "run", runId],
    queryFn: () => getEvalRun(runId),
  });
  const { data, isLoading } = runQuery;
  const run = data?.data;
  if (runQuery.error) {
    const problem = toApiProblem(runQuery.error);
    return (
      <div style={{ marginTop: 16 }}>
        <PageDataState
          status={problemPageStatus(problem)}
          data={null}
          problem={problem}
          isEmpty={(items: EvalGoalResult[]) => items.length === 0}
          onRetry={() => void runQuery.refetch()}
        >
          {() => null}
        </PageDataState>
      </div>
    );
  }
  if (isLoading || !run) return <Card loading style={{ marginTop: 16 }} />;

  const fitness = run.fitness ?? {};
  const goalColumns: ColumnsType<EvalGoalResult> = [
    {
      title: t("evals.goal"),
      dataIndex: "instruction",
      ellipsis: true,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("evals.status"),
      dataIndex: "status",
      width: 120,
      render: (s: string) => {
        const color =
          s === "completed" || s === "approved"
            ? "success"
            : s === "failed"
              ? "error"
              : "default";
        return <Tag color={color}>{s}</Tag>;
      },
    },
    {
      title: t("evals.failureReason"),
      dataIndex: "failure_reason",
      width: 280,
      render: (r: string | null) => (r ? <MonoText>{r}</MonoText> : "—"),
    },
    {
      title: t("evals.cost"),
      dataIndex: "cost",
      width: 100,
      align: "right" as const,
      render: (c: number) => `¥${(c ?? 0).toFixed(4)}`,
    },
  ];

  return (
    <div style={{ marginTop: 16 }}>
      <Row gutter={16}>
        <Col span={4}>
          <Card>
            <Statistic
              title={t("evals.score")}
              value={fitness.score ?? 0}
              precision={4}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card>
            <Statistic
              title={t("evals.deltaVsPrev")}
              value={run.delta_vs_prev ?? 0}
              precision={4}
              valueStyle={{
                color:
                  (run.delta_vs_prev ?? 0) > 0
                    ? "var(--ts-color-success)"
                    : (run.delta_vs_prev ?? 0) < 0
                      ? "var(--ts-color-error)"
                      : undefined,
              }}
              prefix={(run.delta_vs_prev ?? 0) > 0 ? "+" : ""}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card>
            <Statistic
              title={t("evals.successRate")}
              value={(fitness.success_rate ?? 0) * 100}
              precision={1}
              suffix="%"
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card>
            <Statistic
              title={t("evals.auditRate")}
              value={(fitness.audit_rate ?? 0) * 100}
              precision={1}
              suffix="%"
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title={t("evals.target")} value=" " />
            <MonoText style={{ fontSize: 12 }}>{run.target}</MonoText>
            {run.truncated && (
              <Tag color="warning" style={{ marginTop: 4 }}>
                {t("evals.truncated")}
              </Tag>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={16}>
          <Card title={t("evals.goalResults")} size="small">
            <Table
              rowKey={(_, i) => String(i)}
              columns={goalColumns}
              dataSource={run.goal_results ?? []}
              size="small"
              pagination={false}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card title={t("evals.failureDistribution")} size="small">
            {(run.failure_distribution ?? []).length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={t("evals.noFailures")}
              />
            ) : (
              (run.failure_distribution ?? []).map((d) => {
                const total = (run.goal_results ?? []).filter(
                  (g) => g.status === "failed",
                ).length;
                return (
                  <div key={d.reason} style={{ marginBottom: 8 }}>
                    <MonoText style={{ fontSize: 12 }}>{d.reason}</MonoText>
                    <Progress
                      percent={total ? Math.round((d.count / total) * 100) : 0}
                      format={() => String(d.count)}
                      size="small"
                    />
                  </div>
                );
              })
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}

export default function EvalsPage() {
  const t = useT();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const runsQuery = useQuery({
    queryKey: ["evals", "runs"],
    queryFn: () => listEvalRuns(50),
  });
  const setsQuery = useQuery({
    queryKey: ["evals", "sets"],
    queryFn: () => listEvalSets(),
  });
  const runsData = runsQuery.data;
  const setsData = setsQuery.data;
  const isLoading = runsQuery.isLoading;

  const runs = runsData?.data ?? [];
  const sets = setsData?.data ?? [];
  const effectiveRunId = selectedRunId ?? runs[0]?.id ?? null;

  const queryError = runsQuery.error ?? setsQuery.error;
  if (queryError) {
    const problem = toApiProblem(queryError);
    const retry = () => {
      void runsQuery.refetch();
      void setsQuery.refetch();
    };
    return (
      <PageContainer
        title={t("evals.title")}
        titleBadge={<MaturityBadge maturity="beta" />}
      >
        <CapabilityBoundary
          maturity="beta"
          canDo={t("evals.capabilityCanDo")}
          boundary={t("evals.capabilityBoundary")}
        />
        <PageDataState
          status={problemPageStatus(problem)}
          data={null}
          problem={problem}
          isEmpty={(items: EvalRunBrief[]) => items.length === 0}
          onRetry={retry}
        >
          {() => null}
        </PageDataState>
      </PageContainer>
    );
  }

  const runColumns: ColumnsType<EvalRunBrief> = [
    {
      title: "ID",
      dataIndex: "id",
      width: 120,
      render: (id: string) => (
        <Button type="link" size="small" onClick={() => setSelectedRunId(id)}>
          <MonoText>{id.slice(0, 10)}…</MonoText>
        </Button>
      ),
    },
    {
      title: t("evals.evalSet"),
      dataIndex: "eval_set_name",
      render: (n: string | null) =>
        n ?? (
          <Typography.Text type="secondary">
            {t("evals.adhocSample")}
          </Typography.Text>
        ),
    },
    {
      title: t("evals.score"),
      dataIndex: ["fitness", "score"],
      width: 100,
      align: "right" as const,
      render: (s: number) => (s ?? 0).toFixed(4),
    },
    {
      title: "Δ",
      dataIndex: "delta_vs_prev",
      width: 110,
      render: (d: number | null) => <DeltaTag delta={d} />,
    },
    { title: "n", dataIndex: "n", width: 60, align: "right" as const },
    {
      title: t("evals.time"),
      dataIndex: "created_at",
      width: 170,
      render: (ts: string) => formatTime(ts),
    },
  ];

  return (
    <PageContainer
      title={t("evals.title")}
      titleBadge={<MaturityBadge maturity="beta" />}
    >
      <CapabilityBoundary
        maturity="beta"
        canDo={t("evals.capabilityCanDo")}
        boundary={t("evals.capabilityBoundary")}
      />
      <Typography.Paragraph
        type="secondary"
        style={{ marginTop: -12, marginBottom: 16 }}
      >
        {t("evals.subtitle")}
      </Typography.Paragraph>
      {runs.length === 0 && !isLoading ? (
        <Card>
          <Empty
            image={
              <ExperimentOutlined style={{ fontSize: 48, opacity: 0.4 }} />
            }
            description={
              <div>
                <Typography.Paragraph
                  type="secondary"
                  style={{ marginBottom: 8 }}
                >
                  {t("evals.emptyHint")}
                </Typography.Paragraph>
                <MonoText>tianshu evals run</MonoText>
              </div>
            }
          />
        </Card>
      ) : (
        <>
          <Card size="small">
            <Table
              rowKey="id"
              columns={runColumns}
              dataSource={runs}
              loading={isLoading}
              size="small"
              pagination={{ pageSize: 8 }}
              onRow={(record) => ({
                onClick: () => setSelectedRunId(record.id),
                style: { cursor: "pointer" },
              })}
              rowClassName={(record) =>
                record.id === effectiveRunId ? "ant-table-row-selected" : ""
              }
            />
          </Card>
          {effectiveRunId && <RunDetail runId={effectiveRunId} />}
        </>
      )}

      {sets.length > 0 && (
        <Card
          title={t("evals.savedSets")}
          size="small"
          style={{ marginTop: 16 }}
        >
          <Table
            rowKey="name"
            columns={
              [
                { title: t("evals.setName"), dataIndex: "name" },
                {
                  title: t("evals.goalCount"),
                  dataIndex: "goals",
                  width: 100,
                  align: "right" as const,
                  render: (goals: string[]) => goals.length,
                },
                { title: t("evals.source"), dataIndex: "source", width: 120 },
                {
                  title: t("evals.time"),
                  dataIndex: "created_at",
                  width: 170,
                  render: (ts: string) => formatTime(ts),
                },
              ] as ColumnsType<EvalSet>
            }
            dataSource={sets}
            size="small"
            pagination={false}
          />
        </Card>
      )}
    </PageContainer>
  );
}
