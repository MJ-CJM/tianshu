import {
  Card,
  Row,
  Col,
  Statistic,
  Table,
  Tag,
  Space,
  Button,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import MonoText from "../common/MonoText";
import { formatTime, truncateId } from "../../utils/format";
import {
  useEventBusHandlers,
  useEventBusStats,
  useRecentEvents,
} from "../../hooks/useOps";
import type { RecentEvent } from "../../api/types";
import { useT } from "../../i18n";

const { Text } = Typography;

// ==================== Tab 1: EventBus ====================

export function EventBusTab() {
  const t = useT();
  const { data: handlers, isLoading: handlersLoading } = useEventBusHandlers();
  const { data: stats, isLoading: statsLoading } = useEventBusStats();
  const {
    data: recentEvents,
    isLoading: eventsLoading,
    refetch,
  } = useRecentEvents(30);

  // Build handler registration table data
  const handlerRows = handlers
    ? Object.entries(handlers).flatMap(([eventType, entries]) =>
        entries.map((entry, idx) => ({
          key: `${eventType}-${idx}`,
          event_type: eventType,
          handler: entry.handler,
          priority: entry.priority,
        })),
      )
    : [];

  // Build stats cards data
  const totalEvents = stats
    ? Object.values(stats).reduce((a, b) => a + b, 0)
    : 0;
  const eventTypes = stats ? Object.keys(stats).length : 0;

  const eventColumns: ColumnsType<RecentEvent> = [
    {
      title: t("ops.eventbus.table.type"),
      dataIndex: "event_type",
      key: "event_type",
      width: 200,
      render: (v: string) => {
        const color = v.startsWith("hook.")
          ? "orange"
          : v.includes("completed")
            ? "green"
            : v.includes("failed")
              ? "red"
              : v.includes("submitted")
                ? "blue"
                : "default";
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
      render: (v: string) => (
        <MonoText style={{ fontSize: 11 }}>{truncateId(v)}</MonoText>
      ),
    },
    {
      title: t("ops.eventbus.table.memorial"),
      dataIndex: "memorial_id",
      key: "memorial_id",
      width: 120,
      render: (v: string | null) =>
        v ? (
          <MonoText style={{ fontSize: 11 }}>{truncateId(v)}</MonoText>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: t("ops.eventbus.table.detail"),
      dataIndex: "payload",
      key: "payload",
      ellipsis: true,
      render: (v: Record<string, unknown>) => {
        if (!v || Object.keys(v).length === 0)
          return <Text type="secondary">—</Text>;
        const summary = Object.entries(v)
          .map(
            ([k, val]) =>
              `${k}: ${typeof val === "object" ? JSON.stringify(val) : val}`,
          )
          .join(", ");
        return <Text style={{ fontSize: 12 }}>{summary}</Text>;
      },
    },
    {
      title: t("ops.eventbus.table.time"),
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      render: (v: string) => (
        <Text style={{ fontSize: 12 }}>{formatTime(v)}</Text>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Row gutter={16}>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title={t("ops.eventbus.stat.total")}
              value={totalEvents}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title={t("ops.eventbus.stat.types")}
              value={eventTypes}
            />
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
            <Statistic
              title={t("ops.eventbus.stat.recent")}
              value={recentEvents?.length ?? 0}
              loading={eventsLoading}
            />
          </Card>
        </Col>
      </Row>

      {/* Event type distribution */}
      {stats && (
        <Card
          title={t("ops.eventbus.dist")}
          size="small"
          loading={statsLoading}
        >
          <Row gutter={[8, 8]}>
            {Object.entries(stats)
              .sort(([, a], [, b]) => b - a)
              .map(([type, count]) => (
                <Col key={type} span={8}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      padding: "4px 0",
                    }}
                  >
                    <Tag>{type}</Tag>
                    <Text strong>{count}</Text>
                  </div>
                </Col>
              ))}
          </Row>
        </Card>
      )}

      {/* Handler registration table */}
      <Card
        title={t("ops.eventbus.handlersTitle")}
        size="small"
        loading={handlersLoading}
      >
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
              render: (v: string) => (
                <MonoText style={{ fontSize: 12 }}>{v}</MonoText>
              ),
            },
            {
              title: t("ops.eventbus.priority"),
              dataIndex: "priority",
              key: "priority",
              width: 100,
              align: "center" as const,
              render: (v: number) => (
                <Tag
                  color={
                    v <= 10
                      ? "red"
                      : v <= 50
                        ? "orange"
                        : v <= 100
                          ? "blue"
                          : "default"
                  }
                >
                  {v}
                </Tag>
              ),
              sorter: (a: { priority: number }, b: { priority: number }) =>
                a.priority - b.priority,
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
        extra={
          <Button
            icon={<ReloadOutlined />}
            size="small"
            onClick={() => refetch()}
          >
            {t("action.refresh")}
          </Button>
        }
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
