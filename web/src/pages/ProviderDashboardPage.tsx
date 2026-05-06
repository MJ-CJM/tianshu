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
import { useT, type TFunction } from "../i18n";

const { Title } = Typography;

const buildProviderColumns = (
  t: TFunction,
  onDelete: (name: string) => void,
): ColumnsType<ProviderInfo> => [
  {
    title: t("system.providers.table.name"),
    dataIndex: "name",
    key: "name",
    width: 140,
  },
  {
    title: t("system.providers.table.model"),
    dataIndex: "model",
    key: "model",
    width: 160,
  },
  {
    title: t("system.providers.table.status"),
    dataIndex: "status",
    key: "status",
    width: 100,
    render: (v: string) => {
      const color = v === "active" ? "green" : v === "degraded" ? "orange" : "red";
      return <Tag color={color}>{v}</Tag>;
    },
  },
  {
    title: t("system.providers.table.priority"),
    dataIndex: "priority",
    key: "priority",
    width: 80,
    align: "right",
  },
  {
    title: t("system.providers.table.rpm"),
    dataIndex: "rpm_limit",
    key: "rpm_limit",
    width: 100,
    align: "right",
    render: (v: number | null) => v ?? "-",
  },
  {
    title: t("cost.summary.totalCost"),
    dataIndex: "cost_per_1k_prompt",
    key: "cost_per_1k_prompt",
    width: 140,
    align: "right",
    render: (v: number | null) => (v != null ? `¥${v.toFixed(4)}` : "-"),
  },
  {
    title: t("system.skills.table.actions"),
    key: "actions",
    width: 80,
    render: (_, record) => (
      <Popconfirm title={t("system.providers.confirmDeleteProvider")} onConfirm={() => onDelete(record.name)}>
        <Button type="text" danger size="small" icon={<DeleteOutlined />} />
      </Popconfirm>
    ),
  },
];

const buildPluginColumns = (t: TFunction): ColumnsType<PluginInfo> => [
  {
    title: t("system.plugins.table.name"),
    dataIndex: "name",
    key: "name",
    width: 160,
  },
  {
    title: t("system.plugins.table.version"),
    dataIndex: "version",
    key: "version",
    width: 100,
  },
  {
    title: t("system.plugins.table.status"),
    dataIndex: "status",
    key: "status",
    width: 100,
    render: (v: string) => (
      <Tag color={v === "active" ? "green" : "default"}>{v}</Tag>
    ),
  },
  {
    title: t("system.plugins.table.installedAt"),
    dataIndex: "installed_at",
    key: "installed_at",
    width: 180,
    render: (v: string) => (v ? new Date(v).toLocaleString("zh-CN") : "-"),
  },
];

export default function ProviderDashboardPage() {
  const t = useT();
  const { data: providers, isLoading: providersLoading } = useProviders();
  const { data: plugins, isLoading: pluginsLoading } = usePlugins();
  const deleteMutation = useDeleteProvider();

  const handleDelete = (name: string) => {
    deleteMutation.mutate(name, {
      onSuccess: () => notification.success({ message: t("system.toast.providerDeleted", { name }) }),
    });
  };

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Title level={4}>{t("system.providers.listTitle")}</Title>
        <Card>
          {!providers?.length && !providersLoading ? (
            <Empty description={t("system.providers.empty")} />
          ) : (
            <Table<ProviderInfo>
              columns={buildProviderColumns(t, handleDelete)}
              dataSource={providers ?? []}
              rowKey="name"
              loading={providersLoading}
              size="small"
              pagination={false}
            />
          )}
        </Card>

        <Title level={4}>{t("system.tab.plugins")}</Title>
        <Card>
          {!plugins?.length && !pluginsLoading ? (
            <Empty description={t("system.plugins.empty")} />
          ) : (
            <Table<PluginInfo>
              columns={buildPluginColumns(t)}
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
