import React, { useState } from "react";
import {
  Card,
  Space,
  Segmented,
  Table,
  Tabs,
  Tag,
  Input,
  InputNumber,
  Button,
  Empty,
  Popconfirm,
  notification,
  Descriptions,
  Collapse,
  Typography,
  Spin,
  theme,
  Row,
  Col,
  Statistic,
} from "antd";
import {
  DeleteOutlined,
  SearchOutlined,
  TeamOutlined,
  MessageOutlined,
  FileTextOutlined,
  BarChartOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  usePersonaMemory,
  useDeleteMemory,
  useBatchDeleteMemory,
  useRecallMemory,
  useMemoryPolicies,
  usePersonaMemorials,
} from "../hooks/useMemory";
import { useMemoryStats, useCompactMemory, useTriggerReflection } from "../hooks/useOps";
import type { MemoryEntry, EdictMemorialGroup, MemorialBrief } from "../api/types";
import PageContainer from "../components/common/PageContainer";
import { useT } from "../i18n";

const { Text, Paragraph } = Typography;

const PERSONA_IDS = ["bingbu", "neige", "ducha", "tongzheng", "wenyuan", "hubu"] as const;

const categoryColors: Record<string, string> = {
  observation: "blue",
  insight: "gold",
  entity: "green",
  summary: "purple",
};

const sourceColors: Record<string, string> = {
  agent: "default",
  compaction: "cyan",
  reflection: "orange",
};

const statusColors: Record<string, string> = {
  completed: "green",
  failed: "red",
  running: "blue",
  submitted: "default",
  cancelled: "orange",
};

function MemorySummaryTab({ persona }: { persona: string }) {
  const t = useT();
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MemoryEntry[] | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);

  const { data: memories, isLoading } = usePersonaMemory(persona);
  const deleteMutation = useDeleteMemory();
  const batchDeleteMutation = useBatchDeleteMemory();
  const recallMutation = useRecallMemory();
  const { data: policies } = useMemoryPolicies();

  const handleSearch = () => {
    if (!searchQuery.trim()) {
      setSearchResults(null);
      return;
    }
    recallMutation.mutate(
      { persona_id: persona, query: searchQuery, include_shared: true, limit: 30 },
      {
        onSuccess: (data) => setSearchResults(data),
      },
    );
  };

  const handleDelete = (entryId: string) => {
    deleteMutation.mutate(entryId, {
      onSuccess: () => notification.success({ message: t("memory.toast.deleted") }),
    });
  };

  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) return;
    batchDeleteMutation.mutate(selectedRowKeys as string[], {
      onSuccess: (result) => {
        notification.success({ message: t("memory.toast.batchDeleted", { n: result.deleted }) });
        setSelectedRowKeys([]);
      },
      onError: () => notification.error({ message: t("memory.toast.batchDeleteFailed") }),
    });
  };

  const displayData = searchResults ?? memories ?? [];

  const columns: ColumnsType<MemoryEntry> = [
    {
      title: t("memory.table.category"),
      dataIndex: "category",
      key: "category",
      width: 110,
      render: (v: string) => (
        <Tag color={categoryColors[v] ?? "default"}>{v}</Tag>
      ),
      filters: [
        { text: t("memory.category.observation"), value: "observation" },
        { text: t("memory.category.insight"), value: "insight" },
        { text: t("memory.category.entity"), value: "entity" },
        { text: t("memory.category.summary"), value: "summary" },
      ],
      onFilter: (value, record) => record.category === value,
    },
    {
      title: t("memory.table.content"),
      dataIndex: "content",
      key: "content",
      ellipsis: true,
    },
    {
      title: t("memory.table.source"),
      dataIndex: "source",
      key: "source",
      width: 100,
      render: (v: string) => (
        <Tag color={sourceColors[v] ?? "default"}>{v}</Tag>
      ),
    },
    {
      title: t("memory.table.access"),
      dataIndex: "access_level",
      key: "access_level",
      width: 90,
      render: (v: string) => {
        const color = v === "court" ? "red" : v === "shared" ? "orange" : "default";
        return <Tag color={color}>{v}</Tag>;
      },
    },
    {
      title: t("memory.table.confidence"),
      dataIndex: "confidence",
      key: "confidence",
      width: 90,
      align: "right",
      render: (v: number) => `${(v * 100).toFixed(0)}%`,
    },
    {
      title: t("memory.table.time"),
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      render: (v: string) => new Date(v).toLocaleString("zh-CN"),
    },
    {
      title: "",
      key: "actions",
      width: 50,
      render: (_, record) => (
        <Popconfirm
          title={t("memory.selection.confirmDelete")}
          onConfirm={() => handleDelete(record.id)}
        >
          <Button
            type="text"
            danger
            size="small"
            icon={<DeleteOutlined />}
          />
        </Popconfirm>
      ),
    },
  ];

  const currentPolicy = policies?.[persona];
  const personaName = t(`dept.${persona}`);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* Search */}
      <Card size="small">
        <Space.Compact style={{ width: "100%" }}>
          <Input
            placeholder={t("memory.search.placeholder")}
            prefix={<SearchOutlined />}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onPressEnter={handleSearch}
            allowClear
            onClear={() => setSearchResults(null)}
          />
          <Button
            type="primary"
            loading={recallMutation.isPending}
            onClick={handleSearch}
          >
            {t("memory.search.submit")}
          </Button>
        </Space.Compact>
        {searchResults && (
          <Text type="secondary" style={{ marginTop: 8, display: "block" }}>
            {t("memory.search.summary", { n: searchResults.length, q: searchQuery })}
          </Text>
        )}
      </Card>

      {/* Batch action bar */}
      {selectedRowKeys.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Text type="secondary">{t("memory.selection.selected", { n: selectedRowKeys.length })}</Text>
          <Popconfirm
            title={t("memory.selection.confirmBatchDelete", { n: selectedRowKeys.length })}
            onConfirm={handleBatchDelete}
            okText={t("common.confirm")}
            cancelText={t("common.cancel")}
          >
            <Button
              danger
              size="small"
              icon={<DeleteOutlined />}
              loading={batchDeleteMutation.isPending}
            >
              {t("memory.selection.batchDelete")}
            </Button>
          </Popconfirm>
          <Button size="small" onClick={() => setSelectedRowKeys([])}>
            {t("memory.selection.clearSelection")}
          </Button>
        </div>
      )}

      {/* Memory Table */}
      <Card
        title={t("memory.summary", { name: personaName })}
        extra={<Text type="secondary">{t("memory.count", { n: displayData.length })}</Text>}
      >
        {displayData.length === 0 && !isLoading ? (
          <Empty description={t("memory.empty")} />
        ) : (
          <Table<MemoryEntry>
            columns={columns}
            dataSource={displayData}
            rowKey="id"
            loading={isLoading}
            size="small"
            pagination={{ pageSize: 15, showSizeChanger: true }}
            rowSelection={{
              selectedRowKeys,
              onChange: setSelectedRowKeys,
            }}
          />
        )}
      </Card>

      {/* Access Policies */}
      <Collapse
        items={[
          {
            key: "policies",
            label: (
              <Space>
                <TeamOutlined />
                <span>{t("memory.policy.title")}</span>
              </Space>
            ),
            children: currentPolicy ? (
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label={t("memory.policy.canRead")}>
                  {currentPolicy.can_read.length > 0
                    ? currentPolicy.can_read.map((p: string) => (
                        <Tag key={p} color="blue">
                          {t(`dept.${p}`)}
                        </Tag>
                      ))
                    : <Text type="secondary">{t("memory.policy.none")}</Text>}
                </Descriptions.Item>
                <Descriptions.Item label={t("memory.policy.canWrite")}>
                  {currentPolicy.can_write.length > 0
                    ? currentPolicy.can_write.map((p: string) => (
                        <Tag key={p} color="green">
                          {t(`dept.${p}`)}
                        </Tag>
                      ))
                    : <Text type="secondary">{t("memory.policy.none")}</Text>}
                </Descriptions.Item>
                <Descriptions.Item label={t("memory.policy.shareLevel")}>
                  <Tag
                    color={
                      currentPolicy.share_level === "court"
                        ? "red"
                        : currentPolicy.share_level === "shared"
                          ? "orange"
                          : "default"
                    }
                  >
                    {currentPolicy.share_level}
                  </Tag>
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Text type="secondary">{t("memory.policy.missing")}</Text>
            ),
          },
        ]}
      />
    </Space>
  );
}

function MemorialBubble({ memorial }: { memorial: MemorialBrief }) {
  const t = useT();
  const { token } = theme.useToken();
  const isFailed = memorial.status === "failed";

  return (
    <div style={{ marginBottom: 16 }}>
      {/* User instruction */}
      {memorial.instruction && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
          <div
            style={{
              maxWidth: "80%",
              padding: "8px 12px",
              borderRadius: 12,
              borderTopRightRadius: 2,
              background: token.colorPrimaryBg,
              border: `1px solid ${token.colorPrimaryBorder}`,
            }}
          >
            <Text style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
              {memorial.instruction}
            </Text>
          </div>
        </div>
      )}

      {/* AI response */}
      <div style={{ display: "flex", justifyContent: "flex-start" }}>
        <div
          style={{
            maxWidth: "80%",
            padding: "8px 12px",
            borderRadius: 12,
            borderTopLeftRadius: 2,
            background: isFailed ? token.colorErrorBg : token.colorBgContainer,
            border: `1px solid ${isFailed ? token.colorErrorBorder : token.colorBorder}`,
          }}
        >
          {isFailed && memorial.error ? (
            <Text type="danger" style={{ fontSize: 13 }}>
              {memorial.error}
            </Text>
          ) : memorial.result ? (
            <Paragraph
              ellipsis={{ rows: 6, expandable: true, symbol: t("memory.history.expand") }}
              style={{ marginBottom: 0, fontSize: 13, whiteSpace: "pre-wrap" }}
            >
              {memorial.result}
            </Paragraph>
          ) : memorial.summary ? (
            <Text type="secondary" style={{ fontSize: 13 }}>
              {memorial.summary}
            </Text>
          ) : (
            <Text type="secondary" style={{ fontSize: 13 }}>
              <Tag color={statusColors[memorial.status] ?? "default"}>
                {memorial.status}
              </Tag>
            </Text>
          )}
          <div style={{ marginTop: 4, textAlign: "right" }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              {new Date(memorial.created_at).toLocaleString("zh-CN")}
            </Text>
          </div>
        </div>
      </div>
    </div>
  );
}

function ConversationHistoryTab({ persona }: { persona: string }) {
  const t = useT();
  const { data: groups, isLoading } = usePersonaMemorials(persona);

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!groups || groups.length === 0) {
    return <Empty description={t("memory.history.empty")} />;
  }

  return (
    <Collapse
      accordion
      items={groups.map((group: EdictMemorialGroup) => ({
        key: group.edict_id,
        label: (
          <Space>
            <Text strong>{group.edict_title || group.edict_goal}</Text>
            <Tag color={group.edict_status === "completed" ? "green" : group.edict_status === "cancelled" ? "orange" : "blue"}>
              {group.edict_status}
            </Tag>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("memory.history.count", { n: group.memorials.length })}
            </Text>
          </Space>
        ),
        children: (
          <div style={{ padding: "8px 0" }}>
            {group.memorials.map((m: MemorialBrief) => (
              <MemorialBubble key={m.id} memorial={m} />
            ))}
          </div>
        ),
      }))}
    />
  );
}

function MemoryMaintenanceTab({ persona }: { persona: string }) {
  const t = useT();
  const { data: stats } = useMemoryStats();
  const compactMutation = useCompactMemory();
  const reflectMutation = useTriggerReflection();
  const [maxAgeDays, setMaxAgeDays] = useState(7);

  const personaStats = stats?.[persona];

  const handleCompact = () => {
    compactMutation.mutate(
      { personaId: persona, maxAgeDays },
      {
        onSuccess: (resp) => {
          const data = resp.data;
          if (data?.status === "completed") {
            notification.success({
              message: t("memory.toast.compactCompleted"),
              description: t("memory.toast.compactCompletedDesc", { from: data.original_count ?? 0, to: data.compacted_count ?? 0, tokens: data.tokens_saved ?? 0 }),
            });
          } else {
            notification.info({
              message: data?.status === "skipped" ? t("memory.toast.compactSkipped") : t("memory.toast.compactResult"),
              description: data?.reason,
            });
          }
        },
      },
    );
  };

  const handleReflect = () => {
    reflectMutation.mutate(persona, {
      onSuccess: (resp) => {
        const data = resp.data;
        if (data?.status === "completed") {
          notification.success({
            message: t("memory.toast.reflectCompleted"),
            description: t("memory.toast.reflectCompletedDesc", { n: data.insights_generated ?? 0 }),
          });
        } else {
          notification.info({
            message: data?.status === "cooldown" ? t("memory.toast.reflectCooldown") : t("memory.toast.reflectResult"),
            description: data?.reason,
          });
        }
      },
    });
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* Stats */}
      {personaStats && (
        <Row gutter={16}>
          <Col span={6}>
            <Card size="small">
              <Statistic title={t("memory.stats.entries")} value={personaStats.entry_count} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic title={t("memory.stats.tokens")} value={personaStats.estimated_tokens} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic title={t("memory.stats.files")} value={personaStats.markdown_files} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title={t("memory.stats.size")}
                value={(personaStats.markdown_size_bytes / 1024).toFixed(1)}
                suffix="KB"
              />
            </Card>
          </Col>
        </Row>
      )}

      {personaStats?.by_category && (
        <Card title={t("memory.stats.byCategory")} size="small">
          <Row gutter={16}>
            {Object.entries(personaStats.by_category).map(([cat, count]) => (
              <Col key={cat} span={6}>
                <Statistic
                  title={cat}
                  value={count as number}
                  valueStyle={{
                    color: cat === "insight" ? "#faad14" :
                      cat === "observation" ? "#1890ff" :
                      cat === "summary" ? "#722ed1" : "#52c41a",
                  }}
                />
              </Col>
            ))}
          </Row>
        </Card>
      )}

      {/* Operations */}
      <Card title={t("memory.compact.title")} size="small">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text type="secondary">
            {t("memory.compact.desc")}
          </Text>
          <Space>
            <Text>{t("memory.compact.prefix")}</Text>
            <InputNumber
              value={maxAgeDays}
              onChange={(v) => setMaxAgeDays(v ?? 7)}
              min={1}
              max={90}
              style={{ width: 80 }}
            />
            <Text>{t("memory.compact.suffix")}</Text>
            <Button
              type="primary"
              loading={compactMutation.isPending}
              onClick={handleCompact}
            >
              {t("memory.compact.submit")}
            </Button>
          </Space>
        </Space>
      </Card>

      <Card title={t("memory.reflect.title")} size="small">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text type="secondary">
            {t("memory.reflect.desc")}
          </Text>
          <Button
            type="primary"
            loading={reflectMutation.isPending}
            onClick={handleReflect}
          >
            {t("memory.reflect.submit")}
          </Button>
        </Space>
      </Card>
    </Space>
  );
}

export default function MemoryDashboardPage() {
  const t = useT();
  const [persona, setPersona] = useState("bingbu");
  const [activeTab, setActiveTab] = useState("memory");

  const personaOptions = PERSONA_IDS.map((id) => ({
    value: id,
    label: t(`dept.${id}`),
  }));

  return (
    <PageContainer title={t("memory.title")}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Segmented
          value={persona}
          onChange={(v) => {
            setPersona(v as string);
          }}
          options={personaOptions}
          block
        />

        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: "memory",
              label: (
                <Space>
                  <FileTextOutlined />
                  {t("memory.tab.memory")}
                </Space>
              ),
              children: <MemorySummaryTab persona={persona} />,
            },
            {
              key: "history",
              label: (
                <Space>
                  <MessageOutlined />
                  {t("memory.tab.history")}
                </Space>
              ),
              children: <ConversationHistoryTab persona={persona} />,
            },
            {
              key: "maintenance",
              label: (
                <Space>
                  <BarChartOutlined />
                  {t("memory.tab.maintenance")}
                </Space>
              ),
              children: <MemoryMaintenanceTab persona={persona} />,
            },
          ]}
        />
      </Space>
    </PageContainer>
  );
}
