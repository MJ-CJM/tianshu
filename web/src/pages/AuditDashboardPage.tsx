import { Button, Card, Row, Col, Statistic, Table, Tag, Tooltip } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
import { useAuditStats } from "../hooks/useAudit";
import PageContainer from "../components/common/PageContainer";
import MonoText from "../components/common/MonoText";
import { formatTokens, formatTime, truncateId } from "../utils/format";
import {
  PRIORITY_LABELS,
  PRIORITY_COLORS,
  VERDICT_LABELS,
  VERDICT_COLORS,
  REVIEW_STATUS_LABELS,
} from "../utils/constants";
import type { EdictUsageRow, RecentAuditRow } from "../api/types";

export default function AuditDashboardPage() {
  const navigate = useNavigate();
  const { data: stats, isLoading, refetch } = useAuditStats();

  const summary = stats?.summary;
  const audited = (summary?.audit_pass ?? 0) + (summary?.audit_flag ?? 0) + (summary?.audit_block ?? 0);
  const passRate = audited > 0 ? ((summary?.audit_pass ?? 0) / audited) * 100 : 0;
  const flagRate = audited > 0 ? ((summary?.audit_flag ?? 0) / audited) * 100 : 0;

  const usageColumns: ColumnsType<EdictUsageRow> = [
    {
      title: "敕令",
      dataIndex: "edict_title",
      ellipsis: true,
      render: (title: string, record) => (
        <a onClick={() => navigate(`/edicts/${record.edict_id}`)}>{title || truncateId(record.edict_id)}</a>
      ),
    },
    {
      title: "优先级",
      dataIndex: "priority",
      width: 80,
      render: (p: string) => (
        <Tag color={PRIORITY_COLORS[p]}>{PRIORITY_LABELS[p] ?? p}</Tag>
      ),
    },
    {
      title: "奏折数",
      dataIndex: "memorial_count",
      width: 80,
      align: "right",
    },
    {
      title: "Prompt",
      dataIndex: "prompt_tokens",
      width: 100,
      align: "right",
      render: (v: number) => formatTokens(v),
    },
    {
      title: "Completion",
      dataIndex: "completion_tokens",
      width: 100,
      align: "right",
      render: (v: number) => formatTokens(v),
    },
    {
      title: "Total",
      dataIndex: "total_tokens",
      width: 100,
      align: "right",
      render: (v: number) => <strong>{formatTokens(v)}</strong>,
    },
    {
      title: "预算",
      dataIndex: "token_budget",
      width: 120,
      align: "right",
      render: (budget: number | null, record) =>
        budget ? `${formatTokens(record.total_tokens)} / ${formatTokens(budget)}` : "—",
    },
  ];

  const auditColumns: ColumnsType<RecentAuditRow> = [
    {
      title: "奏折编号",
      dataIndex: "memorial_id",
      width: 120,
      render: (id: string) => (
        <MonoText style={{ fontSize: 12 }}>{truncateId(id)}</MonoText>
      ),
    },
    {
      title: "敕令",
      dataIndex: "edict_title",
      ellipsis: true,
      render: (title: string, record) => (
        <a onClick={() => navigate(`/edicts/${record.edict_id}`)}>{title || truncateId(record.edict_id)}</a>
      ),
    },
    {
      title: "审计结论",
      dataIndex: "verdict",
      width: 90,
      render: (v: string) => (
        <Tag color={VERDICT_COLORS[v]}>{VERDICT_LABELS[v] ?? v}</Tag>
      ),
    },
    {
      title: "原因",
      dataIndex: "reasons",
      width: 200,
      ellipsis: true,
      render: (reasons: string[]) => {
        if (!reasons || reasons.length === 0) return "—";
        const text = reasons.join("; ");
        return reasons.length > 1 ? (
          <Tooltip title={reasons.map((r, i) => <div key={i}>{r}</div>)}>
            <span>{text}</span>
          </Tooltip>
        ) : (
          <span>{text}</span>
        );
      },
    },
    {
      title: "复核状态",
      dataIndex: "review_status",
      width: 100,
      render: (s: string) => (
        <Tag>{REVIEW_STATUS_LABELS[s] ?? s}</Tag>
      ),
    },
    {
      title: "时间",
      dataIndex: "completed_at",
      width: 170,
      render: (v: string | null) => formatTime(v),
    },
  ];

  return (
    <PageContainer
      title="审计司"
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
          刷新
        </Button>
      }
    >
      <Row gutter={16}>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="Token 总量"
              value={summary?.total_tokens ?? 0}
              formatter={(v) => formatTokens(Number(v))}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="奏折总数" value={summary?.total_memorials ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="审计通过率"
              value={passRate}
              precision={1}
              suffix="%"
              valueStyle={{ color: "#52c41a" }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="标记率"
              value={flagRate}
              precision={1}
              suffix="%"
              valueStyle={{ color: "#faad14" }}
            />
          </Card>
        </Col>
      </Row>

      <Card title="敕令 Token 用量" style={{ marginTop: 24 }} size="small">
        <Table<EdictUsageRow>
          columns={usageColumns}
          dataSource={stats?.per_edict ?? []}
          rowKey="edict_id"
          loading={isLoading}
          pagination={false}
          size="small"
          locale={{ emptyText: "暂无数据" }}
        />
      </Card>

      <Card title="最近审计结果" style={{ marginTop: 24 }} size="small">
        <Table<RecentAuditRow>
          columns={auditColumns}
          dataSource={stats?.recent_audits ?? []}
          rowKey="memorial_id"
          loading={isLoading}
          pagination={false}
          size="small"
          locale={{ emptyText: "暂无审计记录" }}
        />
      </Card>
    </PageContainer>
  );
}
