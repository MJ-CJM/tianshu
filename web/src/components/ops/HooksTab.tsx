import {
  Card,
  Row,
  Col,
  Statistic,
  Table,
  Tag,
  Space,
  Empty,
  Typography,
} from "antd";
import { BellOutlined } from "@ant-design/icons";
import MonoText from "../common/MonoText";
import { useHooksRegistry, useNotificationChannels } from "../../hooks/useOps";
import type { NotificationChannel } from "../../api/types";
import { useT } from "../../i18n";

const { Text } = Typography;

interface HookRow {
  key: string;
  hook_type: string;
  handler: string;
  priority: number;
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
        })),
      )
    : [];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Row gutter={16}>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title={t("ops.hooks.registered")}
              value={hookRows.length}
            />
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
      <Card
        title={t("ops.hooks.registryTitle")}
        size="small"
        loading={isLoading}
      >
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
                ? Object.keys(hooks).map((hk) => ({
                    text: t(`ops.hooks.label.${hk}`) || hk,
                    value: hk,
                  }))
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
                const color =
                  v <= 10
                    ? "red"
                    : v <= 50
                      ? "orange"
                      : v <= 100
                        ? "blue"
                        : "default";
                const label =
                  v <= 10
                    ? t("ops.hooks.priorityHighest")
                    : v <= 50
                      ? t("ops.hooks.priorityHigh")
                      : v <= 100
                        ? t("ops.hooks.priorityMid")
                        : t("ops.hooks.priorityLow");
                return (
                  <Tag color={color}>
                    {v} ({label})
                  </Tag>
                );
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
                  <Tag
                    color={
                      v >= record.rpm_limit ? "red" : v > 0 ? "blue" : "default"
                    }
                  >
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
