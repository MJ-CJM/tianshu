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
import { useT } from "../i18n";

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
  const t = useT();
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
      title: t("ops.eventbus.table.type"),
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
        ? [...new Set(recentEvents.map((e) => e.event_type))].map((evt) => ({
            text: evt,
            value: evt,
          }))
        : [],
      onFilter: (value, record) => record.event_type === value,
    },
    {
      title: t("ops.eventbus.table.edict"),
      dataIndex: "edict_id",
      key: "edict_id",
      width: 120,
      render: (v: string) => <MonoText style={{ fontSize: 11 }}>{truncateId(v)}</MonoText>,
    },
    {
      title: t("ops.eventbus.table.memorial"),
      dataIndex: "memorial_id",
      key: "memorial_id",
      width: 120,
      render: (v: string | null) =>
        v ? <MonoText style={{ fontSize: 11 }}>{truncateId(v)}</MonoText> : <Text type="secondary">—</Text>,
    },
    {
      title: t("ops.eventbus.table.detail"),
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
      title: t("ops.eventbus.table.time"),
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
            <Statistic title={t("ops.eventbus.stat.total")} value={totalEvents} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t("ops.eventbus.stat.types")} value={eventTypes} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title={t("ops.eventbus.stat.handlers")}
              value={handlerRows.length}
              loading={handlersLoading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t("ops.eventbus.stat.recent")} value={recentEvents?.length ?? 0} loading={eventsLoading} />
          </Card>
        </Col>
      </Row>

      {/* Event type distribution */}
      {stats && (
        <Card title={t("ops.eventbus.dist")} size="small" loading={statsLoading}>
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
      <Card title={t("ops.eventbus.handlersTitle")} size="small" loading={handlersLoading}>
        <Table
          columns={[
            {
              title: t("ops.eventbus.table.type"),
              dataIndex: "event_type",
              key: "event_type",
              width: 200,
              render: (v: string) => <Tag>{v}</Tag>,
            },
            {
              title: t("ops.eventbus.handlerCol"),
              dataIndex: "handler",
              key: "handler",
              render: (v: string) => <MonoText style={{ fontSize: 12 }}>{v}</MonoText>,
            },
            {
              title: t("ops.eventbus.priority"),
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
          locale={{ emptyText: t("ops.eventbus.emptyHandlers") }}
        />
      </Card>

      {/* Recent events */}
      <Card
        title={t("ops.eventbus.recentTitle")}
        size="small"
        extra={<Button icon={<ReloadOutlined />} size="small" onClick={() => refetch()}>{t("action.refresh")}</Button>}
      >
        <Table<RecentEvent>
          columns={eventColumns}
          dataSource={recentEvents ?? []}
          rowKey="id"
          loading={eventsLoading}
          size="small"
          pagination={{ pageSize: 15, showSizeChanger: true }}
          locale={{ emptyText: t("ops.eventbus.emptyEvents") }}
        />
      </Card>
    </Space>
  );
}

// ==================== Tab 2: Workers & Lanes ====================

export function WorkersTab() {
  const t = useT();
  const { data: status, isLoading, refetch } = useWorkersStatus();

  if (isLoading) {
    return <Card loading />;
  }

  if (!status) {
    return <Empty description={t("ops.workers.empty")} />;
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
              title={t("ops.workers.active")}
              value={pool?.active_count ?? 0}
              suffix={`/ ${pool?.max_concurrency ?? 0}`}
              valueStyle={{ color: pool?.active_count > 0 ? "#1890ff" : undefined }}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title={t("ops.workers.maxConcurrency")} value={pool?.max_concurrency ?? 0} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title={t("ops.workers.queue")} value={pool?.pending_count ?? 0} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic
              title={t("ops.workers.completed")}
              value={pool?.completed_count ?? 0}
              valueStyle={{ color: "#52c41a" }}
            />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic
              title={t("ops.workers.failed")}
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
              {t("action.refresh")}
            </Button>
          </Card>
        </Col>
      </Row>

      {/* Global Lane */}
      {globalLane && (
        <Card title={t("ops.workers.globalLane")} size="small">
          <Row gutter={16} align="middle">
            <Col span={8}>
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label={t("ops.workers.maxConcurrency")}>
                  {globalLane.max_concurrency}
                </Descriptions.Item>
                <Descriptions.Item label={t("ops.workers.active2")}>
                  <Badge status={globalLane.active > 0 ? "processing" : "default"} />
                  {globalLane.active}
                </Descriptions.Item>
                <Descriptions.Item label={t("ops.workers.available")}>
                  {globalLane.available}
                </Descriptions.Item>
              </Descriptions>
            </Col>
            <Col span={16}>
              <div style={{ padding: "0 24px" }}>
                <Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
                  {t("ops.workers.slotUsage")}
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
      <Card title={t("ops.workers.sessionLane")} size="small">
        {lanes?.sessions && Object.keys(lanes.sessions).length > 0 ? (
          <Table<SessionLaneRow>
            columns={[
              {
                title: t("ops.workers.edictId"),
                dataIndex: "edict_id",
                key: "edict_id",
                render: (v) => <MonoText style={{ fontSize: 12 }}>{truncateId(v)}</MonoText>,
              },
              {
                title: t("ops.workers.maxConcurrency"),
                dataIndex: "max",
                key: "max",
                width: 100,
                align: "center" as const,
              },
              {
                title: t("ops.workers.availableSlot"),
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
                title: t("ops.workers.status"),
                key: "status",
                width: 100,
                render: (_, record) =>
                  record.available < record.max ? (
                    <Badge status="processing" text={t("ops.workers.running")} />
                  ) : (
                    <Badge status="default" text={t("ops.workers.idle")} />
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
            locale={{ emptyText: t("ops.workers.emptySession") }}
          />
        ) : (
          <Empty description={t("ops.workers.emptySessionDesc")} />
        )}
      </Card>
    </Space>
  );
}

// ==================== Tab 3: Hooks & Channels ====================

export function HooksTab() {
  const t = useT();
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

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Row gutter={16}>
        <Col span={8}>
          <Card size="small">
            <Statistic title={t("ops.hooks.registered")} value={hookRows.length} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title={t("ops.hooks.types")}
              value={hooks ? Object.keys(hooks).length : 0}
              suffix="/ 10"
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title={t("ops.hooks.channels")}
              value={channels?.length ?? 0}
              prefix={<BellOutlined />}
            />
          </Card>
        </Col>
      </Row>

      {/* Hooks table */}
      <Card title={t("ops.hooks.registryTitle")} size="small" loading={isLoading}>
        <Table<HookRow>
          columns={[
            {
              title: t("ops.hooks.type"),
              dataIndex: "hook_type",
              key: "hook_type",
              width: 180,
              render: (v) => (
                <Tag color="purple">{t(`ops.hooks.label.${v}`) || v}</Tag>
              ),
              filters: hooks
                ? Object.keys(hooks).map((hk) => ({ text: t(`ops.hooks.label.${hk}`) || hk, value: hk }))
                : [],
              onFilter: (value, record) => record.hook_type === value,
            },
            {
              title: t("ops.hooks.handler"),
              dataIndex: "handler",
              key: "handler",
              render: (v) => <MonoText style={{ fontSize: 12 }}>{v}</MonoText>,
            },
            {
              title: t("ops.hooks.priority"),
              dataIndex: "priority",
              key: "priority",
              width: 100,
              align: "center" as const,
              render: (v) => {
                const color = v <= 10 ? "red" : v <= 50 ? "orange" : v <= 100 ? "blue" : "default";
                const label = v <= 10 ? t("ops.hooks.priorityHighest") : v <= 50 ? t("ops.hooks.priorityHigh") : v <= 100 ? t("ops.hooks.priorityMid") : t("ops.hooks.priorityLow");
                return <Tag color={color}>{v} ({label})</Tag>;
              },
              sorter: (a, b) => a.priority - b.priority,
            },
          ]}
          dataSource={hookRows}
          rowKey="key"
          size="small"
          pagination={false}
          locale={{ emptyText: t("ops.hooks.emptyRegistry") }}
        />
      </Card>

      {/* Notification Channels */}
      <Card
        title={
          <Space>
            <BellOutlined />
            {t("ops.hooks.channelsTitle")}
          </Space>
        }
        size="small"
      >
        {channels && channels.length > 0 ? (
          <Table<NotificationChannel>
            columns={[
              {
                title: t("ops.hooks.channelName"),
                dataIndex: "name",
                key: "name",
                render: (v: string) => <Text strong>{v}</Text>,
              },
              {
                title: t("ops.hooks.channelType"),
                dataIndex: "type",
                key: "type",
                width: 150,
                render: (v: string) => <Tag color="blue">{v}</Tag>,
              },
              {
                title: t("ops.hooks.rateLimit"),
                dataIndex: "rpm_limit",
                key: "rpm_limit",
                width: 120,
                align: "center" as const,
                render: (v: number) => `${v} / min`,
              },
              {
                title: t("ops.hooks.recentSends"),
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
            locale={{ emptyText: t("ops.hooks.emptyChannels") }}
          />
        ) : (
          <Empty description={t("ops.hooks.emptyChannelsDesc")} />
        )}
      </Card>
    </Space>
  );
}

// ==================== Main Page ====================

export default function OpsMonitorPage() {
  const t = useT();
  const [activeTab, setActiveTab] = useState("eventbus");

  return (
    <PageContainer title={t("ops.title")}>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "eventbus",
            label: (
              <Space>
                <ThunderboltOutlined />
                {t("ops.tab.eventbus")}
              </Space>
            ),
            children: <EventBusTab />,
          },
          {
            key: "workers",
            label: (
              <Space>
                <NodeIndexOutlined />
                {t("ops.tab.workers")}
              </Space>
            ),
            children: <WorkersTab />,
          },
          {
            key: "hooks",
            label: (
              <Space>
                <ApiOutlined />
                {t("ops.tab.hooks")}
              </Space>
            ),
            children: <HooksTab />,
          },
        ]}
      />
    </PageContainer>
  );
}
