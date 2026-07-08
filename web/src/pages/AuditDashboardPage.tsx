import { useCallback, useEffect, useState } from "react";
import { Button, Card, Row, Col, Statistic, Table, Tag, Tooltip, Timeline, Tabs, Space, Descriptions, Select, Input, Typography } from "antd";
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
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuditStats } from "../hooks/useAudit";
import { useAuditRules } from "../hooks/useOps";
import { EventBusTab } from "../components/ops/EventBusTab";
import { WorkersTab } from "../components/ops/WorkersTab";
import { HooksTab } from "../components/ops/HooksTab";
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
import { getFailureDistribution } from "../api/evals";
import type { NetworkEventRow, FailureDistributionItem } from "../api/types";
import { useT } from "../i18n";

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

function FailureAttributionCard() {
  const t = useT();
  const [days, setDays] = useState<number | undefined>(30);
  const { data } = useQuery({
    queryKey: ["evals", "failure-distribution", days],
    queryFn: () => getFailureDistribution(days),
  });
  const dist = data?.data ?? [];
  const total = dist.reduce((acc, d) => acc + d.count, 0);

  return (
    <Card
      title={t("audit.section.failureAttribution")}
      style={{ marginTop: 24 }}
      size="small"
      extra={
        <Select
          size="small"
          value={days ?? 0}
          style={{ width: 120 }}
          onChange={(v: number) => setDays(v === 0 ? undefined : v)}
          options={[
            { value: 7, label: t("audit.failure.days7") },
            { value: 30, label: t("audit.failure.days30") },
            { value: 0, label: t("audit.failure.all") },
          ]}
        />
      }
    >
      {dist.length === 0 ? (
        <Typography.Text type="secondary">{t("audit.failure.none")}</Typography.Text>
      ) : (
        <Table<FailureDistributionItem>
          rowKey="reason"
          size="small"
          pagination={false}
          dataSource={dist}
          columns={[
            {
              title: t("audit.failure.reason"),
              dataIndex: "reason",
              render: (r: string) => <MonoText>{r}</MonoText>,
            },
            {
              title: t("audit.failure.count"),
              dataIndex: "count",
              width: 90,
              align: "right" as const,
            },
            {
              title: t("audit.failure.share"),
              key: "share",
              width: 100,
              align: "right" as const,
              render: (_: unknown, rec: FailureDistributionItem) =>
                total ? `${((rec.count / total) * 100).toFixed(1)}%` : "—",
            },
            {
              title: t("audit.failure.lastSeen"),
              dataIndex: "last_seen",
              width: 180,
              render: (ts: string | null) => (ts ? formatTime(ts) : "—"),
            },
          ]}
        />
      )}
    </Card>
  );
}

function HookEventsCard() {
  const t = useT();
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
          {t("audit.hookEvents.title")}
          <Tag style={{ marginLeft: 8 }}>{events.length}</Tag>
        </span>
      }
      extra={
        <Button
          type="text"
          size="small"
          onClick={() => setCollapsed((v) => !v)}
        >
          {collapsed ? t("audit.hookEvents.expand") : t("audit.hookEvents.collapse")}
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
  const t = useT();
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
      title: t("audit.network.table.time"),
      dataIndex: "created_at",
      width: 170,
      render: (v: string) => formatTime(v),
    },
    {
      title: t("audit.network.table.edict"),
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
      title: t("audit.network.table.tool"),
      dataIndex: "tool",
      width: 110,
      render: (v: string) => <Tag color={toolColors[v] ?? "default"}>{v}</Tag>,
    },
    {
      title: t("audit.network.table.host"),
      dataIndex: "host",
      ellipsis: true,
      render: (v: string | null) => v ?? "—",
    },
    { title: t("audit.network.table.method"), dataIndex: "method", width: 80, render: (v) => v ?? "—" },
    {
      title: t("audit.network.table.http"),
      dataIndex: "http_status",
      width: 90,
      render: (v: number | null, r: NetworkEventRow) => {
        if (v == null) return r.is_error ? <Tag color="red">error</Tag> : "—";
        return <Tag color={v < 400 ? "green" : "red"}>{v}</Tag>;
      },
    },
    {
      title: t("audit.network.table.bytes"),
      dataIndex: "bytes_out",
      width: 90,
      render: (v: number | null) => (v == null ? "—" : v.toLocaleString()),
    },
    {
      title: t("audit.network.table.credential"),
      dataIndex: "credential_name",
      width: 140,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("audit.network.table.cached"),
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
            placeholder={t("audit.network.filter.tool")}
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
            placeholder={t("audit.network.filter.host")}
            style={{ width: 220 }}
            value={host}
            onChange={(e) => setHost(e.target.value)}
            onPressEnter={reload}
            allowClear
          />
          <Select
            placeholder={t("audit.network.filter.status")}
            allowClear
            style={{ width: 120 }}
            value={status}
            onChange={setStatus}
            options={[
              { value: "ok", label: t("audit.network.filter.success") },
              { value: "error", label: t("audit.network.filter.fail") },
            ]}
          />
          <Button onClick={reload} loading={loading}>
            {t("action.refresh")}
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
  const t = useT();
  const navigate = useNavigate();
  const { data: stats, isLoading, refetch } = useAuditStats();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") ?? "stats";
  const setActiveTab = (key: string) => setSearchParams({ tab: key }, { replace: true });
  const { data: rulesData } = useAuditRules();

  const summary = stats?.summary;
  const audited = (summary?.audit_pass ?? 0) + (summary?.audit_flag ?? 0) + (summary?.audit_block ?? 0);
  const passRate = audited > 0 ? ((summary?.audit_pass ?? 0) / audited) * 100 : 0;
  const flagRate = audited > 0 ? ((summary?.audit_flag ?? 0) / audited) * 100 : 0;

  const usageColumns: ColumnsType<EdictUsageRow> = [
    {
      title: t("audit.usageTable.edict"),
      dataIndex: "edict_title",
      ellipsis: true,
      render: (title: string, record) => (
        <a onClick={() => navigate(`/edicts/${record.edict_id}`)}>{title || truncateId(record.edict_id)}</a>
      ),
    },
    {
      title: t("audit.usageTable.priority"),
      dataIndex: "priority",
      width: 80,
      render: (p: string) => (
        <Tag color={PRIORITY_COLORS[p]}>{PRIORITY_LABELS[p] ?? p}</Tag>
      ),
    },
    {
      title: t("audit.usageTable.memorialCount"),
      dataIndex: "memorial_count",
      width: 80,
      align: "right",
    },
    {
      title: t("audit.usageTable.prompt"),
      dataIndex: "prompt_tokens",
      width: 100,
      align: "right",
      render: (v: number) => formatTokens(v),
    },
    {
      title: t("audit.usageTable.completion"),
      dataIndex: "completion_tokens",
      width: 100,
      align: "right",
      render: (v: number) => formatTokens(v),
    },
    {
      title: t("audit.usageTable.total"),
      dataIndex: "total_tokens",
      width: 100,
      align: "right",
      render: (v: number) => <strong>{formatTokens(v)}</strong>,
    },
    {
      title: t("audit.usageTable.budget"),
      dataIndex: "token_budget",
      width: 120,
      align: "right",
      render: (budget: number | null, record) =>
        budget ? `${formatTokens(record.total_tokens)} / ${formatTokens(budget)}` : "—",
    },
  ];

  const auditColumns: ColumnsType<RecentAuditRow> = [
    {
      title: t("audit.recentTable.memorialId"),
      dataIndex: "memorial_id",
      width: 120,
      render: (id: string) => (
        <MonoText style={{ fontSize: 12 }}>{truncateId(id)}</MonoText>
      ),
    },
    {
      title: t("audit.recentTable.edict"),
      dataIndex: "edict_title",
      ellipsis: true,
      render: (title: string, record) => (
        <a onClick={() => navigate(`/edicts/${record.edict_id}`)}>{title || truncateId(record.edict_id)}</a>
      ),
    },
    {
      title: t("audit.recentTable.verdict"),
      dataIndex: "verdict",
      width: 90,
      render: (v: string) => (
        <Tag color={VERDICT_COLORS[v]}>{VERDICT_LABELS[v] ?? v}</Tag>
      ),
    },
    {
      title: t("audit.recentTable.reasons"),
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
      title: t("audit.recentTable.reviewStatus"),
      dataIndex: "review_status",
      width: 100,
      render: (s: string) => (
        <Tag>{REVIEW_STATUS_LABELS[s] ?? s}</Tag>
      ),
    },
    {
      title: t("audit.recentTable.time"),
      dataIndex: "completed_at",
      width: 170,
      render: (v: string | null) => formatTime(v),
    },
  ];

  return (
    <PageContainer
      title={t("audit.title")}
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
          {t("action.refresh")}
        </Button>
      }
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "stats",
            label: t("audit.tab.stats"),
            children: (
              <>
                <Row gutter={16}>
                  <Col span={6}>
                    <Card size="small">
                      <Statistic
                        title={t("audit.stat.totalTokens")}
                        value={summary?.total_tokens ?? 0}
                        formatter={(v) => formatTokens(Number(v))}
                      />
                    </Card>
                  </Col>
                  <Col span={6}>
                    <Card size="small">
                      <Statistic title={t("audit.stat.totalMemorials")} value={summary?.total_memorials ?? 0} />
                    </Card>
                  </Col>
                  <Col span={6}>
                    <Card size="small">
                      <Statistic
                        title={t("audit.stat.passRate")}
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
                        title={t("audit.stat.flagRate")}
                        value={flagRate}
                        precision={1}
                        suffix="%"
                        valueStyle={{ color: "#faad14" }}
                      />
                    </Card>
                  </Col>
                </Row>

                <Card title={t("audit.section.usage")} style={{ marginTop: 24 }} size="small">
                  <Table<EdictUsageRow>
                    columns={usageColumns}
                    dataSource={stats?.per_edict ?? []}
                    rowKey="edict_id"
                    loading={isLoading}
                    pagination={false}
                    size="small"
                    locale={{ emptyText: t("audit.empty.usage") }}
                  />
                </Card>

                <Card title={t("audit.section.recent")} style={{ marginTop: 24 }} size="small">
                  <Table<RecentAuditRow>
                    columns={auditColumns}
                    dataSource={stats?.recent_audits ?? []}
                    rowKey="memorial_id"
                    loading={isLoading}
                    pagination={false}
                    size="small"
                    locale={{ emptyText: t("audit.empty.recent") }}
                  />
                </Card>

                <FailureAttributionCard />

                <HookEventsCard />
              </>
            ),
          },
          {
            key: "policy",
            label: (
              <Space>
                <SafetyOutlined />
                {t("audit.tab.policy")}
              </Space>
            ),
            children: <PolicyDecisionsTab />,
          },
          {
            key: "eventbus",
            label: (
              <Space>
                <ThunderboltOutlined />
                {t("audit.tab.eventbus")}
              </Space>
            ),
            children: <EventBusTab />,
          },
          {
            key: "workers",
            label: (
              <Space>
                <NodeIndexOutlined />
                {t("audit.tab.workers")}
              </Space>
            ),
            children: <WorkersTab />,
          },
          {
            key: "hooks",
            label: (
              <Space>
                <ApiOutlined />
                {t("audit.tab.hooks")}
              </Space>
            ),
            children: <HooksTab />,
          },
          {
            key: "network",
            label: t("audit.tab.network"),
            children: <NetworkEventsTab />,
          },
          {
            key: "rules",
            label: t("audit.tab.rules"),
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Card title={t("audit.section.auditRules")} size="small">
                  <Table
                    columns={[
                      { title: t("audit.rulesTable.name"), dataIndex: "name", key: "name" },
                      { title: t("audit.rulesTable.description"), dataIndex: "description", key: "description" },
                      {
                        title: t("audit.rulesTable.severity"),
                        dataIndex: "severity",
                        key: "severity",
                        width: 100,
                        render: (v: string) => (
                          <Tag color={v === "block" ? "red" : v === "flag" ? "orange" : "default"}>{v}</Tag>
                        ),
                      },
                      {
                        title: t("audit.rulesTable.status"),
                        dataIndex: "enabled",
                        key: "enabled",
                        width: 80,
                        render: (v: boolean) => (
                          <Tag color={v ? "green" : "default"}>{v ? t("audit.rulesTable.enabled") : t("audit.rulesTable.disabled")}</Tag>
                        ),
                      },
                    ]}
                    dataSource={rulesData?.rules ?? []}
                    rowKey="id"
                    size="small"
                    pagination={false}
                    locale={{ emptyText: t("audit.empty.rules") }}
                  />
                </Card>

                <Card title={t("audit.section.reviewPolicies")} size="small">
                  <Descriptions column={1} bordered size="small">
                    {(rulesData?.review_policies ?? []).map((p: ReviewPolicyInfo) => (
                      <Descriptions.Item key={p.value} label={<Tag color="blue">{p.label}</Tag>}>
                        {p.description}{t("audit.reviewPolicy.valuePrefix")}<MonoText>{p.value}</MonoText>{t("audit.reviewPolicy.valueSuffix")}
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
