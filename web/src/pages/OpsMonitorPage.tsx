import { useState } from "react";
import {
  Tabs,
  Card,
  Row,
  Col,
  Statistic,
  Table,
  Tag,
  Badge,
  Progress,
  Space,
  Button,
  Descriptions,
  Empty,
  Typography,
} from "antd";
import {
  ReloadOutlined,
  ThunderboltOutlined,
  NodeIndexOutlined,
  ApiOutlined,
  BellOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import PageContainer from "../components/common/PageContainer";
import MonoText from "../components/common/MonoText";
import { formatTime, truncateId } from "../utils/format";
import {
  useEventBusHandlers,
  useEventBusStats,
  useRecentEvents,
  useHooksRegistry,
  useWorkersStatus,
  useNotificationChannels,
} from "../hooks/useOps";
import type { RecentEvent, NotificationChannel } from "../api/types";

const { Text } = Typography;

interface SessionLaneRow {
  key: string;
  edict_id: string;
  max: number;
  available: number;
}

interface HookRow {
  key: string;
  hook_type: string;
  handler: string;
  priority: number;
}

// ==================== Tab 1: EventBus ====================

export function EventBusTab() {
  const { data: handlers, isLoading: handlersLoading } = useEventBusHandlers();
  const { data: stats, isLoading: statsLoading } = useEventBusStats();
  const { data: recentEvents, isLoading: eventsLoading, refetch } = useRecentEvents(30);

  // Build handler registration table data
  const handlerRows = handlers
    ? Object.entries(handlers).flatMap(([eventType, entries]) =>
        entries.map((entry, idx) => ({
          key: `${eventType}-${idx}`,
          event_type: eventType,
          handler: entry.handler,
          priority: entry.priority,
        }))
      )
    : [];

  // Build stats cards data
  const totalEvents = stats ? Object.values(stats).reduce((a, b) => a + b, 0) : 0;
  const eventTypes = stats ? Object.keys(stats).length : 0;

  const eventColumns: ColumnsType<RecentEvent> = [
    {
      title: "事件类型",
      dataIndex: "event_type",
      key: "event_type",
      width: 200,
      render: (v: string) => {
        const color = v.startsWith("hook.") ? "orange" :
          v.includes("completed") ? "green" :
          v.includes("failed") ? "red" :
          v.includes("submitted") ? "blue" : "default";
        return <Tag color={color}>{v}</Tag>;
      },
      filters: recentEvents
        ? [...new Set(recentEvents.map((e) => e.event_type))].map((t) => ({
            text: t,
            value: t,
          }))
        : [],
      onFilter: (value, record) => record.event_type === value,
    },
    {
      title: "敕令",
      dataIndex: "edict_id",
      key: "edict_id",
      width: 120,
      render: (v: string) => <MonoText style={{ fontSize: 11 }}>{truncateId(v)}</MonoText>,
    },
    {
      title: "奏折",
      dataIndex: "memorial_id",
      key: "memorial_id",
      width: 120,
      render: (v: string | null) =>
        v ? <MonoText style={{ fontSize: 11 }}>{truncateId(v)}</MonoText> : <Text type="secondary">—</Text>,
    },
    {
      title: "详情",
      dataIndex: "payload",
      key: "payload",
      ellipsis: true,
      render: (v: Record<string, unknown>) => {
        if (!v || Object.keys(v).length === 0) return <Text type="secondary">—</Text>;
        const summary = Object.entries(v)
          .map(([k, val]) => `${k}: ${typeof val === "object" ? JSON.stringify(val) : val}`)
          .join(", ");
        return <Text style={{ fontSize: 12 }}>{summary}</Text>;
      },
    },
    {
      title: "时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      render: (v: string) => <Text style={{ fontSize: 12 }}>{formatTime(v)}</Text>,
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Row gutter={16}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="事件总数" value={totalEvents} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="事件类型数" value={eventTypes} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="已注册 Handler"
              value={handlerRows.length}
              loading={handlersLoading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="最近事件" value={recentEvents?.length ?? 0} loading={eventsLoading} />
          </Card>
        </Col>
      </Row>

      {/* Event type distribution */}
      {stats && (
        <Card title="事件类型分布" size="small" loading={statsLoading}>
          <Row gutter={[8, 8]}>
            {Object.entries(stats)
              .sort(([, a], [, b]) => b - a)
              .map(([type, count]) => (
                <Col key={type} span={8}>
                  <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0" }}>
                    <Tag>{type}</Tag>
                    <Text strong>{count}</Text>
                  </div>
                </Col>
              ))}
          </Row>
        </Card>
      )}

      {/* Handler registration table */}
      <Card title="已注册 Handler" size="small" loading={handlersLoading}>
        <Table
          columns={[
            {
              title: "事件类型",
              dataIndex: "event_type",
              key: "event_type",
              width: 200,
              render: (v: string) => <Tag>{v}</Tag>,
            },
            {
              title: "Handler",
              dataIndex: "handler",
              key: "handler",
              render: (v: string) => <MonoText style={{ fontSize: 12 }}>{v}</MonoText>,
            },
            {
              title: "优先级",
              dataIndex: "priority",
              key: "priority",
              width: 100,
              align: "center" as const,
              render: (v: number) => (
                <Tag color={v <= 10 ? "red" : v <= 50 ? "orange" : v <= 100 ? "blue" : "default"}>
                  {v}
                </Tag>
              ),
              sorter: (a: { priority: number }, b: { priority: number }) => a.priority - b.priority,
            },
          ]}
          dataSource={handlerRows}
          rowKey="key"
          size="small"
          pagination={false}
          locale={{ emptyText: "暂无注册" }}
        />
      </Card>

      {/* Recent events */}
      <Card
        title="最近事件流"
        size="small"
        extra={<Button icon={<ReloadOutlined />} size="small" onClick={() => refetch()}>刷新</Button>}
      >
        <Table<RecentEvent>
          columns={eventColumns}
          dataSource={recentEvents ?? []}
          rowKey="id"
          loading={eventsLoading}
          size="small"
          pagination={{ pageSize: 15, showSizeChanger: true }}
          locale={{ emptyText: "暂无事件" }}
        />
      </Card>
    </Space>
  );
}

// ==================== Tab 2: Workers & Lanes ====================

export function WorkersTab() {
  const { data: status, isLoading, refetch } = useWorkersStatus();

  if (isLoading) {
    return <Card loading />;
  }

  if (!status) {
    return <Empty description="无法获取 Worker 状态" />;
  }

  const pool = status.pool;
  const lanes = status.lanes;
  const globalLane = lanes?.global;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {/* Pool statistics */}
      <Row gutter={16}>
        <Col span={4}>
          <Card size="small">
            <Statistic
              title="活跃任务"
              value={pool?.active_count ?? 0}
              suffix={`/ ${pool?.max_concurrency ?? 0}`}
              valueStyle={{ color: pool?.active_count > 0 ? "#1890ff" : undefined }}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="最大并发" value={pool?.max_concurrency ?? 0} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="排队中" value={pool?.pending_count ?? 0} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic
              title="已完成"
              value={pool?.completed_count ?? 0}
              valueStyle={{ color: "#52c41a" }}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic
              title="失败"
              value={pool?.failed_count ?? 0}
              valueStyle={{ color: pool?.failed_count > 0 ? "#ff4d4f" : undefined }}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Button
              icon={<ReloadOutlined />}
              onClick={() => refetch()}
              style={{ width: "100%", height: "100%" }}
            >
              刷新
            </Button>
          </Card>
        </Col>
      </Row>

      {/* Global Lane */}
      {globalLane && (
        <Card title="GlobalLane — 系统级并发" size="small">
          <Row gutter={16} align="middle">
            <Col span={8}>
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="最大并发">
                  {globalLane.max_concurrency}
                </Descriptions.Item>
                <Descriptions.Item label="活跃">
                  <Badge status={globalLane.active > 0 ? "processing" : "default"} />
                  {globalLane.active}
                </Descriptions.Item>
                <Descriptions.Item label="可用">
                  {globalLane.available}
                </Descriptions.Item>
              </Descriptions>
            </Col>
            <Col span={16}>
              <div style={{ padding: "0 24px" }}>
                <Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
                  槽位占用
                </Text>
                <Progress
                  percent={Math.round((globalLane.active / globalLane.max_concurrency) * 100)}
                  steps={globalLane.max_concurrency}
                  strokeColor={globalLane.active > globalLane.max_concurrency * 0.8 ? "#ff4d4f" : "#1890ff"}
                />
              </div>
            </Col>
          </Row>
        </Card>
      )}

      {/* Session Lanes */}
      <Card title="SessionLane — 敕令级并发" size="small">
        {lanes?.sessions && Object.keys(lanes.sessions).length > 0 ? (
          <Table<SessionLaneRow>
            columns={[
              {
                title: "敕令 ID",
                dataIndex: "edict_id",
                key: "edict_id",
                render: (v) => <MonoText style={{ fontSize: 12 }}>{truncateId(v)}</MonoText>,
              },
              {
                title: "最大并发",
                dataIndex: "max",
                key: "max",
                width: 100,
                align: "center" as const,
              },
              {
                title: "可用槽",
                dataIndex: "available",
                key: "available",
                width: 100,
                align: "center" as const,
                render: (v, record) => (
                  <Tag color={v === 0 ? "red" : v < record.max ? "orange" : "green"}>
                    {v} / {record.max}
                  </Tag>
                ),
              },
              {
                title: "状态",
                key: "status",
                width: 100,
                render: (_, record) =>
                  record.available < record.max ? (
                    <Badge status="processing" text="执行中" />
                  ) : (
                    <Badge status="default" text="空闲" />
                  ),
              },
            ]}
            dataSource={Object.entries(lanes.sessions).map(([eid, lane]) => ({
              key: eid,
              edict_id: eid,
              ...(lane as { max: number; available: number }),
            }))}
            size="small"
            pagination={false}
            locale={{ emptyText: "暂无活跃会话" }}
          />
        ) : (
          <Empty description="暂无活跃的 Session Lane" />
        )}
      </Card>
    </Space>
  );
}

// ==================== Tab 3: Hooks & Channels ====================

export function HooksTab() {
  const { data: hooks, isLoading } = useHooksRegistry();
  const { data: channels } = useNotificationChannels();

  // Build hook rows
  const hookRows = hooks
    ? Object.entries(hooks).flatMap(([hookType, entries]) =>
        entries.map((entry, idx) => ({
          key: `${hookType}-${idx}`,
          hook_type: hookType,
          handler: entry.handler,
          priority: entry.priority,
        }))
      )
    : [];

  // Hook type labels
  const hookLabels: Record<string, string> = {
    before_agent_start: "启动前",
    before_tool_call: "工具调用前",
    after_tool_call: "工具调用后",
    llm_input: "LLM 输入",
    llm_output: "LLM 输出",
    agent_end: "执行结束",
    before_iteration: "迭代前",
    before_compaction: "压缩前",
    session_start: "会话开始",
    session_end: "会话结束",
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Row gutter={16}>
        <Col span={8}>
          <Card size="small">
            <Statistic title="已注册 Hook" value={hookRows.length} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title="Hook 类型"
              value={hooks ? Object.keys(hooks).length : 0}
              suffix="/ 10"
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title="通知渠道"
              value={channels?.length ?? 0}
              prefix={<BellOutlined />}
            />
          </Card>
        </Col>
      </Row>

      {/* Hooks table */}
      <Card title="Hooks 注册列表" size="small" loading={isLoading}>
        <Table<HookRow>
          columns={[
            {
              title: "Hook 类型",
              dataIndex: "hook_type",
              key: "hook_type",
              width: 180,
              render: (v) => (
                <Tag color="purple">{hookLabels[v] ?? v}</Tag>
              ),
              filters: hooks
                ? Object.keys(hooks).map((t) => ({ text: hookLabels[t] ?? t, value: t }))
                : [],
              onFilter: (value, record) => record.hook_type === value,
            },
            {
              title: "Handler",
              dataIndex: "handler",
              key: "handler",
              render: (v) => <MonoText style={{ fontSize: 12 }}>{v}</MonoText>,
            },
            {
              title: "优先级",
              dataIndex: "priority",
              key: "priority",
              width: 100,
              align: "center" as const,
              render: (v) => {
                const color = v <= 10 ? "red" : v <= 50 ? "orange" : v <= 100 ? "blue" : "default";
                const label = v <= 10 ? "最高" : v <= 50 ? "高" : v <= 100 ? "中" : "低";
                return <Tag color={color}>{v} ({label})</Tag>;
              },
              sorter: (a, b) => a.priority - b.priority,
            },
          ]}
          dataSource={hookRows}
          rowKey="key"
          size="small"
          pagination={false}
          locale={{ emptyText: "暂无注册" }}
        />
      </Card>

      {/* Notification Channels */}
      <Card
        title={
          <Space>
            <BellOutlined />
            通知渠道
          </Space>
        }
        size="small"
      >
        {channels && channels.length > 0 ? (
          <Table<NotificationChannel>
            columns={[
              {
                title: "渠道名称",
                dataIndex: "name",
                key: "name",
                render: (v: string) => <Text strong>{v}</Text>,
              },
              {
                title: "类型",
                dataIndex: "type",
                key: "type",
                width: 150,
                render: (v: string) => <Tag color="blue">{v}</Tag>,
              },
              {
                title: "速率限制",
                dataIndex: "rpm_limit",
                key: "rpm_limit",
                width: 120,
                align: "center" as const,
                render: (v: number) => `${v} / min`,
              },
              {
                title: "近期发送",
                dataIndex: "recent_sends",
                key: "recent_sends",
                width: 120,
                align: "center" as const,
                render: (v: number, record: NotificationChannel) => (
                  <Tag color={v >= record.rpm_limit ? "red" : v > 0 ? "blue" : "default"}>
                    {v}
                  </Tag>
                ),
              },
            ]}
            dataSource={channels}
            rowKey="name"
            size="small"
            pagination={false}
            locale={{ emptyText: "暂无注册渠道" }}
          />
        ) : (
          <Empty description="暂无注册的通知渠道" />
        )}
      </Card>
    </Space>
  );
}

// ==================== Main Page ====================

export default function OpsMonitorPage() {
  const [activeTab, setActiveTab] = useState("eventbus");

  return (
    <PageContainer title="运维监控台">
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
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
        ]}
      />
    </PageContainer>
  );
}
