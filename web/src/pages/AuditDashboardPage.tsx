import { useState } from "react";
import { Button, Card, Row, Col, Statistic, Table, Tag, Tooltip, Timeline, Tabs, Space, Descriptions } from "antd";
import {
  ReloadOutlined,
  ThunderboltOutlined,
  NodeIndexOutlined,
  ApiOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuditStats } from "../hooks/useAudit";
import { useAuditRules } from "../hooks/useOps";
import { EventBusTab, WorkersTab, HooksTab } from "./OpsMonitorPage";
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
import type { EdictUsageRow, RecentAuditRow, ReviewPolicyInfo } from "../api/types";
import apiClient from "../api/client";

interface HookEvent {
  id: string;
  event_type: string;
  edict_id: string;
  memorial_id: string | null;
  payload: {
    handler?: string;
    blocked?: boolean;
    error?: string | null;
  };
  created_at: string;
}

function HookEventsCard() {
  // Fetch recent hook events from the most recent edicts
  const { data: recentEdicts } = useQuery({
    queryKey: ["edicts", "recent"],
    queryFn: async () => {
      const resp = await apiClient.get("/edicts?limit=5");
      return resp.data?.data ?? [];
    },
    staleTime: 30000,
  });

  const edictIds: string[] = (recentEdicts ?? []).map((e: { id: string }) => e.id);

  const { data: hookEvents, isLoading } = useQuery({
    queryKey: ["hookEvents", edictIds],
    queryFn: async () => {
      const allEvents: HookEvent[] = [];
      for (const eid of edictIds) {
        try {
          const resp = await apiClient.get(`/edicts/${eid}/events`);
          const events: HookEvent[] = (resp.data?.data ?? []).filter(
            (e: HookEvent) => e.event_type.startsWith("hook.")
          );
          allEvents.push(...events);
        } catch {
          // skip
        }
      }
      return allEvents
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
        .slice(0, 20);
    },
    enabled: edictIds.length > 0,
    staleTime: 15000,
  });

  const events = hookEvents ?? [];
  if (events.length === 0 && !isLoading) return null;

  return (
    <Card
      title={
        <span>
          <ThunderboltOutlined style={{ marginRight: 8 }} />
          Hook 触发记录
        </span>
      }
      style={{ marginTop: 24 }}
      size="small"
      loading={isLoading}
    >
      <Timeline
        items={events.map((evt) => ({
          color: evt.payload.error ? "red" : evt.payload.blocked ? "orange" : "green",
          children: (
            <div style={{ fontSize: 13 }}>
              <Tag>{evt.event_type.replace("hook.", "")}</Tag>
              <MonoText style={{ fontSize: 11 }}>{evt.payload.handler ?? "—"}</MonoText>
              {evt.payload.blocked && <Tag color="orange" style={{ marginLeft: 4 }}>blocked</Tag>}
              {evt.payload.error && <Tag color="red" style={{ marginLeft: 4 }}>{evt.payload.error}</Tag>}
              <span style={{ color: "#888", marginLeft: 8, fontSize: 11 }}>
                {formatTime(evt.created_at)}
              </span>
            </div>
          ),
        }))}
      />
    </Card>
  );
}

export default function AuditDashboardPage() {
  const navigate = useNavigate();
  const { data: stats, isLoading, refetch } = useAuditStats();
  const [activeTab, setActiveTab] = useState("stats");
  const { data: rulesData } = useAuditRules();

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
      title="都察院"
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
          刷新
        </Button>
      }
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "stats",
            label: "审计统计",
            children: (
              <>
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

                <HookEventsCard />
              </>
            ),
          },
          {
            key: "eventbus",
            label: (
              <Space>
                <ThunderboltOutlined />
                事件流
              </Space>
            ),
            children: <EventBusTab />,
          },
          {
            key: "workers",
            label: (
              <Space>
                <NodeIndexOutlined />
                并发控制
              </Space>
            ),
            children: <WorkersTab />,
          },
          {
            key: "hooks",
            label: (
              <Space>
                <ApiOutlined />
                Hooks & 通知
              </Space>
            ),
            children: <HooksTab />,
          },
          {
            key: "rules",
            label: "规则管理",
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Card title="审计规则" size="small">
                  <Table
                    columns={[
                      { title: "规则名称", dataIndex: "name", key: "name" },
                      { title: "描述", dataIndex: "description", key: "description" },
                      {
                        title: "严重级别",
                        dataIndex: "severity",
                        key: "severity",
                        width: 100,
                        render: (v: string) => (
                          <Tag color={v === "block" ? "red" : v === "flag" ? "orange" : "default"}>{v}</Tag>
                        ),
                      },
                      {
                        title: "状态",
                        dataIndex: "enabled",
                        key: "enabled",
                        width: 80,
                        render: (v: boolean) => (
                          <Tag color={v ? "green" : "default"}>{v ? "启用" : "禁用"}</Tag>
                        ),
                      },
                    ]}
                    dataSource={rulesData?.rules ?? []}
                    rowKey="id"
                    size="small"
                    pagination={false}
                    locale={{ emptyText: "暂无规则" }}
                  />
                </Card>

                <Card title="审阅策略" size="small">
                  <Descriptions column={1} bordered size="small">
                    {(rulesData?.review_policies ?? []).map((p: ReviewPolicyInfo) => (
                      <Descriptions.Item key={p.value} label={<Tag color="blue">{p.label}</Tag>}>
                        {p.description}（值：<MonoText>{p.value}</MonoText>）
                      </Descriptions.Item>
                    ))}
                  </Descriptions>
                </Card>
              </Space>
            ),
          },
        ]}
      />
    </PageContainer>
  );
}
