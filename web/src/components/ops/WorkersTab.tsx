import {
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
import { ReloadOutlined } from "@ant-design/icons";
import MonoText from "../common/MonoText";
import { truncateId } from "../../utils/format";
import { useWorkersStatus } from "../../hooks/useOps";
import { useT } from "../../i18n";

const { Text } = Typography;

interface SessionLaneRow {
  key: string;
  edict_id: string;
  max: number;
  available: number;
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
