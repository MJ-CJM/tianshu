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

function CabinetOverview({ cabinetPersonas }: { cabinetPersonas: PersonaInfo[] }) {
  const { token } = theme.useToken();

  if (cabinetPersonas.length === 0) {
    return (
      <Card title="内阁概览" size="small">
        <Typography.Text type="secondary">未找到内阁 persona</Typography.Text>
      </Card>
    );
  }

  return (
    <Card title="内阁概览" size="small">
      <Row gutter={[16, 12]}>
        {cabinetPersonas.map((p) => (
          <Col key={p.id} span={8}>
            <Card size="small" style={{ background: token.colorBgContainerDisabled }}>
              <Statistic title={p.name} value={p.id} valueStyle={{ fontSize: 14 }} />
              <Tag color="orange" style={{ marginTop: 4, fontSize: 11 }}>
                {p.llm_config_name || "全局默认"}
              </Tag>
            </Card>
          </Col>
        ))}
      </Row>
      <div style={{ marginTop: 12 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          规划阈值：goal &lt; 100 字 且无约束/输出格式 → 跳过规划（直通 bingbu）
        </Typography.Text>
      </div>
    </Card>
  );
}

export default function CabinetPage() {
  const { data: personas, isLoading: personasLoading } = usePersonas();
  const { data: plannerStats, isLoading: statsLoading } = usePlannerStats();
  const { token } = theme.useToken();

  const cabinetPersonas = (personas ?? []).filter((p: PersonaInfo) => p.department === "neige");

  const historyColumns = [
    {
      title: "敕令",
      dataIndex: "title",
      key: "title",
      ellipsis: true,
      render: (v: string, r: PlannerHistoryItem) => (
        <a href={`/edicts/${r.edict_id}`}>{v || r.goal.slice(0, 30)}</a>
      ),
    },
    {
      title: "规划结果",
      dataIndex: "plan_type",
      key: "plan_type",
      width: 120,
      render: (v: string) => (
        <Tag color={v === "dag" ? "blue" : "green"}>
          {v === "dag" ? "DAG 规划" : "直通"}
        </Tag>
      ),
    },
    {
      title: "规划官",
      dataIndex: "planner_persona_id",
      key: "planner_persona_id",
      width: 120,
      render: (v: string | null) =>
        v ? <Tag color="purple">{v}</Tag> : <Tag>全局</Tag>,
    },
    {
      title: "指派",
      dataIndex: "assigned_persona_id",
      key: "assigned_persona_id",
      width: 100,
      render: (v: string | null) =>
        v ? <Tag color="orange">{v}</Tag> : <Tag>自动</Tag>,
    },
    {
      title: "子任务数",
      dataIndex: "task_count",
      key: "task_count",
      width: 80,
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 160,
      render: (v: string) => formatTime(v),
    },
  ];

  if (personasLoading) {
    return (
      <PageContainer title="内阁">
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer title="内阁">
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <CabinetOverview cabinetPersonas={cabinetPersonas} />

        <Card title="外部感知（鸿胪寺）" size="small">
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              读网页 / 关键词搜索 / 调第三方 API / 结构化抽取；凭证由藏兵阁加密托管，LLM 全程不可见。
            </Typography.Paragraph>
            <Space wrap>
              <Tag color="blue">web_fetch</Tag>
              <Tag color="blue">web_search</Tag>
              <Tag color="geekblue">api_request</Tag>
              <Tag color="purple">web_extract</Tag>
            </Space>
            <Space style={{ marginTop: 4 }}>
              <Typography.Link href="/system?tab=external-creds">
                管理外部凭证 →
              </Typography.Link>
              <Typography.Link href="/edicts" style={{ marginLeft: 12 }}>
                在 Edict 中启用 →
              </Typography.Link>
            </Space>
          </Space>
        </Card>

        {/* 规划统计 */}
        <Card title="规划统计" size="small" loading={statsLoading}>
          {plannerStats && (
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title="总敕令数" value={plannerStats.total_edicts} />
              </Col>
              <Col span={6}>
                <Statistic
                  title="直通（跳过规划）"
                  value={plannerStats.passthrough_count}
                  valueStyle={{ color: token.colorSuccess }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="DAG 生成"
                  value={plannerStats.dag_count}
                  valueStyle={{ color: token.colorPrimary }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="均子任务数"
                  value={plannerStats.avg_tasks_per_dag}
                  precision={1}
                />
              </Col>
            </Row>
          )}
        </Card>

        {/* 规划历史 */}
        <Card title="近期规划记录" size="small">
          <Table
            columns={historyColumns}
            dataSource={(plannerStats?.recent_history ?? []).map((h) => ({
              key: h.edict_id,
              ...h,
            }))}
            size="small"
            pagination={false}
            loading={statsLoading}
            locale={{ emptyText: "暂无规划记录" }}
          />
        </Card>
      </Space>
    </PageContainer>
  );
}
