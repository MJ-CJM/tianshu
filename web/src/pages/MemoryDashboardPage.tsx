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

const { Text, Paragraph } = Typography;

const PERSONAS = [
  { value: "bingbu", label: "兵部" },
  { value: "neige", label: "内阁" },
  { value: "ducha", label: "都察院" },
  { value: "tongzheng", label: "通政司" },
  { value: "wenyuan", label: "文渊阁" },
  { value: "hubu", label: "户部" },
];

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
      onSuccess: () => notification.success({ message: "记忆已删除" }),
    });
  };

  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) return;
    batchDeleteMutation.mutate(selectedRowKeys as string[], {
      onSuccess: (result) => {
        notification.success({ message: `已删除 ${result.deleted} 条记忆` });
        setSelectedRowKeys([]);
      },
      onError: () => notification.error({ message: "批量删除失败" }),
    });
  };

  const displayData = searchResults ?? memories ?? [];

  const columns: ColumnsType<MemoryEntry> = [
    {
      title: "分类",
      dataIndex: "category",
      key: "category",
      width: 110,
      render: (v: string) => (
        <Tag color={categoryColors[v] ?? "default"}>{v}</Tag>
      ),
      filters: [
        { text: "观察", value: "observation" },
        { text: "洞察", value: "insight" },
        { text: "实体", value: "entity" },
        { text: "摘要", value: "summary" },
      ],
      onFilter: (value, record) => record.category === value,
    },
    {
      title: "内容",
      dataIndex: "content",
      key: "content",
      ellipsis: true,
    },
    {
      title: "来源",
      dataIndex: "source",
      key: "source",
      width: 100,
      render: (v: string) => (
        <Tag color={sourceColors[v] ?? "default"}>{v}</Tag>
      ),
    },
    {
      title: "权限",
      dataIndex: "access_level",
      key: "access_level",
      width: 90,
      render: (v: string) => {
        const color = v === "court" ? "red" : v === "shared" ? "orange" : "default";
        return <Tag color={color}>{v}</Tag>;
      },
    },
    {
      title: "置信度",
      dataIndex: "confidence",
      key: "confidence",
      width: 90,
      align: "right",
      render: (v: number) => `${(v * 100).toFixed(0)}%`,
    },
    {
      title: "时间",
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
          title="确定删除此记忆？"
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

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* Search */}
      <Card size="small">
        <Space.Compact style={{ width: "100%" }}>
          <Input
            placeholder="搜索记忆..."
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
            检索
          </Button>
        </Space.Compact>
        {searchResults && (
          <Text type="secondary" style={{ marginTop: 8, display: "block" }}>
            找到 {searchResults.length} 条匹配 &quot;{searchQuery}&quot; 的记忆
          </Text>
        )}
      </Card>

      {/* Batch action bar */}
      {selectedRowKeys.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Text type="secondary">已选 {selectedRowKeys.length} 条</Text>
          <Popconfirm
            title={`确认删除选中的 ${selectedRowKeys.length} 条记忆？`}
            onConfirm={handleBatchDelete}
            okText="确认"
            cancelText="取消"
          >
            <Button
              danger
              size="small"
              icon={<DeleteOutlined />}
              loading={batchDeleteMutation.isPending}
            >
              批量删除
            </Button>
          </Popconfirm>
          <Button size="small" onClick={() => setSelectedRowKeys([])}>
            取消选择
          </Button>
        </div>
      )}

      {/* Memory Table */}
      <Card
        title={`${PERSONAS.find((p) => p.value === persona)?.label ?? persona} — 记忆`}
        extra={<Text type="secondary">{displayData.length} 条</Text>}
      >
        {displayData.length === 0 && !isLoading ? (
          <Empty description="暂无记忆" />
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
                <span>访问策略</span>
              </Space>
            ),
            children: currentPolicy ? (
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="可读取">
                  {currentPolicy.can_read.length > 0
                    ? currentPolicy.can_read.map((p: string) => (
                        <Tag key={p} color="blue">
                          {PERSONAS.find((x) => x.value === p)?.label ?? p}
                        </Tag>
                      ))
                    : <Text type="secondary">无</Text>}
                </Descriptions.Item>
                <Descriptions.Item label="可写入">
                  {currentPolicy.can_write.length > 0
                    ? currentPolicy.can_write.map((p: string) => (
                        <Tag key={p} color="green">
                          {PERSONAS.find((x) => x.value === p)?.label ?? p}
                        </Tag>
                      ))
                    : <Text type="secondary">无</Text>}
                </Descriptions.Item>
                <Descriptions.Item label="共享级别">
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
              <Text type="secondary">该官员暂无策略配置</Text>
            ),
          },
        ]}
      />
    </Space>
  );
}

function MemorialBubble({ memorial }: { memorial: MemorialBrief }) {
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
              ellipsis={{ rows: 6, expandable: true, symbol: "展开" }}
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
  const { data: groups, isLoading } = usePersonaMemorials(persona);

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!groups || groups.length === 0) {
    return <Empty description="暂无对话历史" />;
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
              {group.memorials.length} 条对话
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
              message: "压缩完成",
              description: `${data.original_count} → ${data.compacted_count} 条，节省约 ${data.tokens_saved} tokens`,
            });
          } else {
            notification.info({
              message: data?.status === "skipped" ? "跳过压缩" : "压缩结果",
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
            message: "反思完成",
            description: `生成 ${data.insights_generated} 条洞察`,
          });
        } else {
          notification.info({
            message: data?.status === "cooldown" ? "冷却中" : "反思结果",
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
              <Statistic title="记忆条目" value={personaStats.entry_count} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic title="估算 Token" value={personaStats.estimated_tokens} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic title="Markdown 文件" value={personaStats.markdown_files} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="文件大小"
                value={(personaStats.markdown_size_bytes / 1024).toFixed(1)}
                suffix="KB"
              />
            </Card>
          </Col>
        </Row>
      )}

      {personaStats?.by_category && (
        <Card title="分类统计" size="small">
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
      <Card title="记忆压缩" size="small">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text type="secondary">
            将超过指定天数的旧记忆压缩为摘要，节省 Token 用量。
          </Text>
          <Space>
            <Text>压缩</Text>
            <InputNumber
              value={maxAgeDays}
              onChange={(v) => setMaxAgeDays(v ?? 7)}
              min={1}
              max={90}
              style={{ width: 80 }}
            />
            <Text>天前的记忆</Text>
            <Button
              type="primary"
              loading={compactMutation.isPending}
              onClick={handleCompact}
            >
              执行压缩
            </Button>
          </Space>
        </Space>
      </Card>

      <Card title="记忆反思" size="small">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text type="secondary">
            使用 LLM 从观察记忆中提取洞察和模式。每个官员有 1 小时冷却期。
          </Text>
          <Button
            type="primary"
            loading={reflectMutation.isPending}
            onClick={handleReflect}
          >
            触发反思
          </Button>
        </Space>
      </Card>
    </Space>
  );
}

export default function MemoryDashboardPage() {
  const [persona, setPersona] = useState("bingbu");
  const [activeTab, setActiveTab] = useState("memory");

  return (
    <PageContainer title="文渊阁">
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Segmented
          value={persona}
          onChange={(v) => {
            setPersona(v as string);
          }}
          options={PERSONAS}
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
                  记忆摘要
                </Space>
              ),
              children: <MemorySummaryTab persona={persona} />,
            },
            {
              key: "history",
              label: (
                <Space>
                  <MessageOutlined />
                  对话历史
                </Space>
              ),
              children: <ConversationHistoryTab persona={persona} />,
            },
            {
              key: "maintenance",
              label: (
                <Space>
                  <BarChartOutlined />
                  维护
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
