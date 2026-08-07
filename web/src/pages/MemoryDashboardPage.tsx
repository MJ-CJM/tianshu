import React, { useState } from "react";
import {
  Card,
  Space,
  Select,
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
import { usePersonas } from "../hooks/usePersonas";
import type { MemoryEntry, EdictMemorialGroup, MemorialBrief } from "../api/types";
import PageContainer from "../components/common/PageContainer";
import { useT } from "../i18n";
import PageQueryError from "../components/states/PageQueryError";

const { Text, Paragraph } = Typography;

/** 六个建制部门；其余主体由 /memory/stats 动态发现（见 personaOptions）。 */
const DEPARTMENT_IDS: readonly string[] = [
  "bingbu",
  "neige",
  "ducha",
  "tongzheng",
  "wenyuan",
  "hubu",
];
/** 全朝廷共享池（memory_write scope="court" 的落盘处）。 */
const COURT_ID = "court";

/**
 * 记忆主体的显示名。三类主体共用一个 persona_id 命名空间，不能一律套
 * `dept.*`——court 和官员 id 都没有对应词条，会把原始 key（如 `dept.court`）
 * 直接显示给用户。
 */
function usePersonaLabel() {
  const t = useT();
  const { data: personas } = usePersonas();
  return React.useCallback(
    (id: string) => {
      if (id === COURT_ID) return t("memory.scope.court");
      if (DEPARTMENT_IDS.includes(id)) return t(`dept.${id}`);
      return personas?.find((p) => p.id === id)?.name ?? id;
    },
    [t, personas],
  );
}

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
  const labelOf = usePersonaLabel();
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MemoryEntry[] | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);

  const memoriesQuery = usePersonaMemory(persona);
  const { data: memories, isLoading } = memoriesQuery;
  const deleteMutation = useDeleteMemory();
  const batchDeleteMutation = useBatchDeleteMemory();
  const recallMutation = useRecallMemory();
  const policiesQuery = useMemoryPolicies();
  const { data: policies } = policiesQuery;

  const handleSearch = () => {
    if (!searchQuery.trim()) {
      setSearchResults(null);
      return;
    }
    recallMutation.mutate(
      { persona_id: persona, query: searchQuery, include_shared: true, limit: 30 },
      {
        onSuccess: (data) => setSearchResults(data),
        onError: () => notification.error({ message: t("memory.search.failed") }),
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
  const queryError = memoriesQuery.error ?? policiesQuery.error;

  if (queryError) {
    return (
      <PageQueryError
        error={queryError}
        onRetry={() => {
          void memoriesQuery.refetch();
          void policiesQuery.refetch();
        }}
      />
    );
  }

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
  const personaName = labelOf(persona);

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
              // a11y：同 EdictTable，行内选择框需可读名称，否则屏幕阅读器只念
              // "复选框"。记忆条目无标题，取正文首 20 字作辨识。
              getCheckboxProps: (record) => ({
                name: `memory-entry-${record.id}`,
                "aria-label": t("memory.table.selectRow", {
                  name: record.content.slice(0, 20),
                }),
              }),
            }}
            // 内容列 ellipsis 截断，长条目只能看到开头；展开行给全文与出处，
            // 点行内任意处即可展开（不必非得点左侧箭头）。
            expandable={{
              expandedRowRender: (record) => (
                <Space direction="vertical" size="small" style={{ width: "100%" }}>
                  <Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
                    {record.content}
                  </Paragraph>
                  <Descriptions size="small" column={2}>
                    <Descriptions.Item label={t("memory.table.time")}>
                      {new Date(record.created_at).toLocaleString()}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("memory.table.confidence")}>
                      {(record.confidence * 100).toFixed(0)}%
                    </Descriptions.Item>
                    {record.edict_id ? (
                      <Descriptions.Item label={t("memory.detail.edict")}>
                        <Text code copyable={{ text: record.edict_id }}>
                          #{record.edict_id.slice(0, 8)}
                        </Text>
                      </Descriptions.Item>
                    ) : null}
                    <Descriptions.Item label={t("memory.detail.id")}>
                      <Text code copyable={{ text: record.id }}>
                        {record.id.slice(0, 16)}…
                      </Text>
                    </Descriptions.Item>
                  </Descriptions>
                </Space>
              ),
              expandRowByClick: true,
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
                          {labelOf(p)}
                        </Tag>
                      ))
                    : <Text type="secondary">{t("memory.policy.none")}</Text>}
                </Descriptions.Item>
                <Descriptions.Item label={t("memory.policy.canWrite")}>
                  {currentPolicy.can_write.length > 0
                    ? currentPolicy.can_write.map((p: string) => (
                        <Tag key={p} color="green">
                          {labelOf(p)}
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
  const groupsQuery = usePersonaMemorials(persona);
  const { data: groups, isLoading } = groupsQuery;

  if (groupsQuery.error) {
    return (
      <PageQueryError
        error={groupsQuery.error}
        onRetry={() => void groupsQuery.refetch()}
      />
    );
  }

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
  const statsQuery = useMemoryStats();
  const { data: stats } = statsQuery;
  const compactMutation = useCompactMemory();
  const reflectMutation = useTriggerReflection();
  const [maxAgeDays, setMaxAgeDays] = useState(7);

  if (statsQuery.error) {
    return (
      <PageQueryError
        error={statsQuery.error}
        onRetry={() => void statsQuery.refetch()}
      />
    );
  }

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
                    color: cat === "insight" ? "var(--ts-color-warning)" :
                      cat === "observation" ? "var(--ts-color-info)" :
                      cat === "summary" ? "var(--ts-status-planning)" : "var(--ts-color-success)",
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
  const [persona, setPersona] = useState(COURT_ID);
  const [activeTab, setActiveTab] = useState("memory");
  const { data: memoryStats } = useMemoryStats();
  const labelOf = usePersonaLabel();

  // 记忆按 persona_id 落盘，三种 scope 共用一个命名空间：部门（department）、
  // 官员私有（self）、全朝廷共享（court）。此前这里写死六个部门 id，导致官员
  // 私有记忆与朝廷共享池在界面上完全不可见（写进去了却查不到）。
  // 改为以 /memory/stats 实际发现的主体为准，分三组呈现。
  const personaOptions = React.useMemo(() => {
    const ids = Object.keys(memoryStats ?? {});
    const countOf = (id: string) => memoryStats?.[id]?.entry_count ?? 0;
    const withCount = (text: string, id: string) => `${text} · ${countOf(id)}`;

    const groups: { label: string; options: { value: string; label: string }[] }[] = [];

    if (ids.includes(COURT_ID)) {
      groups.push({
        label: t("memory.scope.court"),
        options: [
          { value: COURT_ID, label: withCount(t("memory.scope.court"), COURT_ID) },
        ],
      });
    }

    const departmentIds = ids.filter((id) => DEPARTMENT_IDS.includes(id));
    if (departmentIds.length > 0) {
      groups.push({
        label: t("memory.scope.department"),
        options: departmentIds.map((id) => ({
          value: id,
          label: withCount(labelOf(id), id),
        })),
      });
    }

    const officialIds = ids.filter(
      (id) => id !== COURT_ID && !DEPARTMENT_IDS.includes(id),
    );
    if (officialIds.length > 0) {
      groups.push({
        label: t("memory.scope.official"),
        options: officialIds.map((id) => ({
          value: id,
          label: withCount(labelOf(id), id),
        })),
      });
    }

    return groups;
  }, [memoryStats, labelOf, t]);

  return (
    <PageContainer title={t("memory.title")}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Select
          value={persona}
          onChange={setPersona}
          options={personaOptions}
          style={{ width: "100%" }}
          showSearch
          optionFilterProp="label"
          placeholder={t("memory.scope.placeholder")}
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
