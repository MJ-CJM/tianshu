import { useCallback, useEffect, useState } from "react";
import { Button, Card, Row, Col, Statistic, Table, Tag, Tooltip, Timeline, Tabs, Space, Descriptions, Select, Input } from "antd";
import {
  ReloadOutlined,
  ThunderboltOutlined,
  NodeIndexOutlined,
  ApiOutlined,
  SafetyOutlined,
  DownOutlined,
  RightOutlined,
} from "@ant-design/icons";
import { fetchPolicyStats } from "../api/policy";
import type { PolicyStats } from "../api/policy";
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
import { listNetworkEvents } from "../api/network_events";
import type { NetworkEventRow } from "../api/types";

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
  const [collapsed, setCollapsed] = useState(true);
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
        <span
          style={{ cursor: "pointer", userSelect: "none" }}
          onClick={() => setCollapsed((v) => !v)}
        >
          {collapsed ? (
            <RightOutlined style={{ marginRight: 8, fontSize: 12 }} />
          ) : (
            <DownOutlined style={{ marginRight: 8, fontSize: 12 }} />
          )}
          <ThunderboltOutlined style={{ marginRight: 8 }} />
          Hook 触发记录
          <Tag style={{ marginLeft: 8 }}>{events.length}</Tag>
        </span>
      }
      extra={
        <Button
          type="text"
          size="small"
          onClick={() => setCollapsed((v) => !v)}
        >
          {collapsed ? "展开" : "折叠"}
        </Button>
      }
      style={{ marginTop: 24 }}
      size="small"
      loading={isLoading}
    >
      {!collapsed && (
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
      )}
    </Card>
  );
}

function PolicyDecisionsTab() {
  const [stats, setStats] = useState<PolicyStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetchPolicyStats().then((s) => {
        if (!cancelled) setStats(s);
      });
    };
    load();
    const timer = setInterval(load, 10_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <Row gutter={16}>
      <Col span={4}>
        <Card size="small">
          <Statistic
            title="Allow"
            value={stats?.allow ?? 0}
            valueStyle={{ color: "#52c41a" }}
          />
        </Card>
      </Col>
      <Col span={4}>
        <Card size="small">
          <Statistic
            title="Deny"
            value={stats?.deny ?? 0}
            valueStyle={{ color: "#ff4d4f" }}
          />
        </Card>
      </Col>
      <Col span={4}>
        <Card size="small">
          <Statistic
            title="Require Approval"
            value={stats?.require_approval ?? 0}
            valueStyle={{ color: "#fa8c16" }}
          />
        </Card>
      </Col>
      <Col span={4}>
        <Card size="small">
          <Statistic title="Approved" value={stats?.approved ?? 0} />
        </Card>
      </Col>
      <Col span={4}>
        <Card size="small">
          <Statistic title="Rejected" value={stats?.rejected ?? 0} />
        </Card>
      </Col>
    </Row>
  );
}

function NetworkEventsTab() {
  const [rows, setRows] = useState<NetworkEventRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [tool, setTool] = useState<string | undefined>(undefined);
  const [host, setHost] = useState<string>("");
  const [status, setStatus] = useState<"ok" | "error" | undefined>(undefined);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listNetworkEvents({
        limit: 200,
        tool,
        host: host.trim() || undefined,
        status,
      });
      setRows(data);
    } finally {
      setLoading(false);
    }
  }, [tool, host, status]);

  useEffect(() => {
    reload();
  }, [reload]);

  const toolColors: Record<string, string> = {
    web_fetch: "blue",
    web_search: "cyan",
    api_request: "geekblue",
    web_extract: "purple",
  };

  const columns: ColumnsType<NetworkEventRow> = [
    {
      title: "时间",
      dataIndex: "created_at",
      width: 170,
      render: (v: string) => formatTime(v),
    },
    {
      title: "敕令",
      dataIndex: "edict_title",
      width: 180,
      ellipsis: true,
      render: (v: string | null, r) => (
        <a onClick={() => (window.location.href = `/edicts/${r.edict_id}`)}>
          {v || truncateId(r.edict_id)}
        </a>
      ),
    },
    {
      title: "工具",
      dataIndex: "tool",
      width: 110,
      render: (v: string) => <Tag color={toolColors[v] ?? "default"}>{v}</Tag>,
    },
    {
      title: "Host",
      dataIndex: "host",
      ellipsis: true,
      render: (v: string | null) => v ?? "—",
    },
    { title: "方法", dataIndex: "method", width: 80, render: (v) => v ?? "—" },
    {
      title: "HTTP",
      dataIndex: "http_status",
      width: 90,
      render: (v: number | null, r: NetworkEventRow) => {
        if (v == null) return r.is_error ? <Tag color="red">error</Tag> : "—";
        return <Tag color={v < 400 ? "green" : "red"}>{v}</Tag>;
      },
    },
    {
      title: "字节",
      dataIndex: "bytes_out",
      width: 90,
      render: (v: number | null) => (v == null ? "—" : v.toLocaleString()),
    },
    {
      title: "凭证",
      dataIndex: "credential_name",
      width: 140,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: "缓存",
      dataIndex: "cached",
      width: 70,
      render: (v: boolean) => (v ? <Tag color="green">yes</Tag> : "—"),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card size="small">
        <Space wrap>
          <Select
            placeholder="工具"
            allowClear
            style={{ width: 160 }}
            value={tool}
            onChange={setTool}
            options={[
              { value: "web_fetch", label: "web_fetch" },
              { value: "web_search", label: "web_search" },
              { value: "api_request", label: "api_request" },
              { value: "web_extract", label: "web_extract" },
            ]}
          />
          <Input
            placeholder="host (精确匹配)"
            style={{ width: 220 }}
            value={host}
            onChange={(e) => setHost(e.target.value)}
            onPressEnter={reload}
            allowClear
          />
          <Select
            placeholder="状态"
            allowClear
            style={{ width: 120 }}
            value={status}
            onChange={setStatus}
            options={[
              { value: "ok", label: "成功" },
              { value: "error", label: "失败" },
            ]}
          />
          <Button onClick={reload} loading={loading}>
            刷新
          </Button>
        </Space>
      </Card>

      <Table<NetworkEventRow>
        rowKey="event_id"
        columns={columns}
        dataSource={rows}
        loading={loading}
        size="small"
        pagination={{ pageSize: 50, showSizeChanger: false }}
      />
    </Space>
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
            key: "policy",
            label: (
              <Space>
                <SafetyOutlined />
                Policy Decisions
              </Space>
            ),
            children: <PolicyDecisionsTab />,
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
            key: "network",
            label: "鸿胪寺访问",
            children: <NetworkEventsTab />,
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
