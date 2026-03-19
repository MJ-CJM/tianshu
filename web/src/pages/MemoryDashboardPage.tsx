import React, { useState } from "react";
import {
  Card,
  Space,
  Segmented,
  Table,
  Tag,
  Input,
  Button,
  Empty,
  Popconfirm,
  notification,
  Descriptions,
  Collapse,
  Typography,
} from "antd";
import {
  DeleteOutlined,
  SearchOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { usePersonaMemory, useDeleteMemory, useBatchDeleteMemory, useRecallMemory, useMemoryPolicies } from "../hooks/useMemory";
import type { MemoryEntry } from "../api/types";
import PageContainer from "../components/common/PageContainer";

const { Text } = Typography;

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

export default function MemoryDashboardPage() {
  const [persona, setPersona] = useState("bingbu");
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

  // Build policy display
  const currentPolicy = policies?.[persona];

  return (
    <PageContainer title="文渊阁">
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Segmented
          value={persona}
          onChange={(v) => {
            setPersona(v as string);
            setSearchResults(null);
            setSearchQuery("");
            setSelectedRowKeys([]);
          }}
          options={PERSONAS}
          block
        />

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
              找到 {searchResults.length} 条匹配 "{searchQuery}" 的记忆
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
          extra={
            <Text type="secondary">
              {displayData.length} 条
            </Text>
          }
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
                      ? currentPolicy.can_read.map((p) => (
                          <Tag key={p} color="blue">
                            {PERSONAS.find((x) => x.value === p)?.label ?? p}
                          </Tag>
                        ))
                      : <Text type="secondary">无</Text>}
                  </Descriptions.Item>
                  <Descriptions.Item label="可写入">
                    {currentPolicy.can_write.length > 0
                      ? currentPolicy.can_write.map((p) => (
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
    </PageContainer>
  );
}
