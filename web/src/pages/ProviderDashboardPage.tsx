import {
  Typography,
  Table,
  Tag,
  Card,
  Space,
  Button,
  Popconfirm,
  Empty,
  notification,
} from "antd";
import { DeleteOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { useProviders, useDeleteProvider, usePlugins } from "../hooks/useProviders";
import type { ProviderInfo, PluginInfo } from "../api/types";

const { Title } = Typography;

const providerColumns = (
  onDelete: (name: string) => void,
): ColumnsType<ProviderInfo> => [
  {
    title: "名称",
    dataIndex: "name",
    key: "name",
    width: 140,
  },
  {
    title: "模型",
    dataIndex: "model",
    key: "model",
    width: 160,
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    width: 100,
    render: (v: string) => {
      const color = v === "active" ? "green" : v === "degraded" ? "orange" : "red";
      return <Tag color={color}>{v}</Tag>;
    },
  },
  {
    title: "优先级",
    dataIndex: "priority",
    key: "priority",
    width: 80,
    align: "right",
  },
  {
    title: "RPM 限制",
    dataIndex: "rpm_limit",
    key: "rpm_limit",
    width: 100,
    align: "right",
    render: (v: number | null) => v ?? "-",
  },
  {
    title: "每千 Token 成本",
    dataIndex: "cost_per_1k_prompt",
    key: "cost_per_1k_prompt",
    width: 140,
    align: "right",
    render: (v: number | null) => (v != null ? `¥${v.toFixed(4)}` : "-"),
  },
  {
    title: "操作",
    key: "actions",
    width: 80,
    render: (_, record) => (
      <Popconfirm title="确定删除此 Provider？" onConfirm={() => onDelete(record.name)}>
        <Button type="text" danger size="small" icon={<DeleteOutlined />} />
      </Popconfirm>
    ),
  },
];

const pluginColumns: ColumnsType<PluginInfo> = [
  {
    title: "名称",
    dataIndex: "name",
    key: "name",
    width: 160,
  },
  {
    title: "版本",
    dataIndex: "version",
    key: "version",
    width: 100,
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    width: 100,
    render: (v: string) => (
      <Tag color={v === "active" ? "green" : "default"}>{v}</Tag>
    ),
  },
  {
    title: "安装时间",
    dataIndex: "installed_at",
    key: "installed_at",
    width: 180,
    render: (v: string) => (v ? new Date(v).toLocaleString("zh-CN") : "-"),
  },
];

export default function ProviderDashboardPage() {
  const { data: providers, isLoading: providersLoading } = useProviders();
  const { data: plugins, isLoading: pluginsLoading } = usePlugins();
  const deleteMutation = useDeleteProvider();

  const handleDelete = (name: string) => {
    deleteMutation.mutate(name, {
      onSuccess: () => notification.success({ message: `Provider "${name}" 已删除` }),
    });
  };

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Title level={4}>Provider 列表</Title>
        <Card>
          {!providers?.length && !providersLoading ? (
            <Empty description="暂无 Provider" />
          ) : (
            <Table<ProviderInfo>
              columns={providerColumns(handleDelete)}
              dataSource={providers ?? []}
              rowKey="name"
              loading={providersLoading}
              size="small"
              pagination={false}
            />
          )}
        </Card>

        <Title level={4}>插件列表</Title>
        <Card>
          {!plugins?.length && !pluginsLoading ? (
            <Empty description="暂无插件" />
          ) : (
            <Table<PluginInfo>
              columns={pluginColumns}
              dataSource={plugins ?? []}
              rowKey="name"
              loading={pluginsLoading}
              size="small"
              pagination={false}
            />
          )}
        </Card>
      </Space>
    </div>
  );
}
