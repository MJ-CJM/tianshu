import {
  Row,
  Col,
  Tag,
  Space,
  Typography,
  Statistic,
  Spin,
  Table,
  Card,
  theme,
} from "antd";
import PageContainer from "../components/common/PageContainer";
import { usePersonas } from "../hooks/usePersonas";
import { usePlannerStats } from "../hooks/useOps";
import { formatTime } from "../utils/format";
import type { PersonaInfo, PlannerHistoryItem } from "../api/types";
import { useT } from "../i18n";
import PageQueryError from "../components/states/PageQueryError";

function CabinetOverview({ cabinetPersonas }: { cabinetPersonas: PersonaInfo[] }) {
  const t = useT();
  const { token } = theme.useToken();

  if (cabinetPersonas.length === 0) {
    return (
      <Card title={t("cabinet.overview")} size="small">
        <Typography.Text type="secondary">{t("cabinet.noPersonas")}</Typography.Text>
      </Card>
    );
  }

  return (
    <Card title={t("cabinet.overview")} size="small">
      <Row gutter={[16, 12]}>
        {cabinetPersonas.map((p) => (
          <Col key={p.id} span={8}>
            <Card size="small" style={{ background: token.colorBgContainerDisabled }}>
              <Statistic title={p.name} value={p.id} valueStyle={{ fontSize: 14 }} />
              <Tag color="orange" style={{ marginTop: 4, fontSize: 11 }}>
                {p.llm_config_name || t("cabinet.globalDefault")}
              </Tag>
            </Card>
          </Col>
        ))}
      </Row>
      <div style={{ marginTop: 12 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t("cabinet.thresholdHint")}
        </Typography.Text>
      </div>
    </Card>
  );
}

export default function CabinetPage() {
  const t = useT();
  const personasQuery = usePersonas();
  const plannerStatsQuery = usePlannerStats();
  const { data: personas, isLoading: personasLoading } = personasQuery;
  const { data: plannerStats, isLoading: statsLoading } = plannerStatsQuery;
  const { token } = theme.useToken();

  const queryError = personasQuery.error ?? plannerStatsQuery.error;
  if (queryError) {
    return (
      <PageContainer title={t("cabinet.title")}>
        <PageQueryError
          error={queryError}
          onRetry={() => {
            void personasQuery.refetch();
            void plannerStatsQuery.refetch();
          }}
        />
      </PageContainer>
    );
  }

  const cabinetPersonas = (personas ?? []).filter((p: PersonaInfo) => p.department === "neige");

  const historyColumns = [
    {
      title: t("cabinet.table.edict"),
      dataIndex: "title",
      key: "title",
      ellipsis: true,
      render: (v: string, r: PlannerHistoryItem) => (
        <a href={`/edicts/${r.edict_id}`}>{v || r.goal.slice(0, 30)}</a>
      ),
    },
    {
      title: t("cabinet.table.planType"),
      dataIndex: "plan_type",
      key: "plan_type",
      width: 120,
      render: (v: string) => (
        <Tag color={v === "dag" ? "blue" : "green"}>
          {v === "dag" ? t("cabinet.planType.dag") : t("cabinet.planType.passthrough")}
        </Tag>
      ),
    },
    {
      title: t("cabinet.table.planner"),
      dataIndex: "planner_persona_id",
      key: "planner_persona_id",
      width: 120,
      render: (v: string | null) =>
        v ? <Tag color="purple">{v}</Tag> : <Tag>{t("cabinet.tag.global")}</Tag>,
    },
    {
      title: t("cabinet.table.assigned"),
      dataIndex: "assigned_persona_id",
      key: "assigned_persona_id",
      width: 100,
      render: (v: string | null) =>
        v ? <Tag color="orange">{v}</Tag> : <Tag>{t("cabinet.tag.auto")}</Tag>,
    },
    {
      title: t("cabinet.table.taskCount"),
      dataIndex: "task_count",
      key: "task_count",
      width: 80,
    },
    {
      title: t("cabinet.table.createdAt"),
      dataIndex: "created_at",
      key: "created_at",
      width: 160,
      render: (v: string) => formatTime(v),
    },
  ];

  if (personasLoading) {
    return (
      <PageContainer title={t("cabinet.title")}>
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer title={t("cabinet.title")}>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <CabinetOverview cabinetPersonas={cabinetPersonas} />

        <Card title={t("cabinet.stats")} size="small" loading={statsLoading}>
          {plannerStats && (
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title={t("cabinet.stat.total")} value={plannerStats.total_edicts} />
              </Col>
              <Col span={6}>
                <Statistic
                  title={t("cabinet.stat.passthrough")}
                  value={plannerStats.passthrough_count}
                  valueStyle={{ color: token.colorSuccess }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title={t("cabinet.stat.dag")}
                  value={plannerStats.dag_count}
                  valueStyle={{ color: token.colorPrimary }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title={t("cabinet.stat.avgTasks")}
                  value={plannerStats.avg_tasks_per_dag}
                  precision={1}
                />
              </Col>
            </Row>
          )}
        </Card>

        <Card title={t("cabinet.history")} size="small">
          <Table
            columns={historyColumns}
            dataSource={(plannerStats?.recent_history ?? []).map((h) => ({
              key: h.edict_id,
              ...h,
            }))}
            size="small"
            pagination={false}
            loading={statsLoading}
            locale={{ emptyText: t("cabinet.empty") }}
          />
        </Card>
      </Space>
    </PageContainer>
  );
}
