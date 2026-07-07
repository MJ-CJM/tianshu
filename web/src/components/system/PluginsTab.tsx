import { Table, Tag, Typography } from "antd";
import { usePlugins } from "../../hooks/useProviders";
import type { PluginInfo } from "../../api/types";
import { useT } from "../../i18n";

export default function PluginsTab() {
  const t = useT();
  const { data: plugins, isLoading } = usePlugins();

  const columns: import("antd/es/table").ColumnsType<PluginInfo> = [
    { title: t("system.plugins.table.name"), dataIndex: "name", key: "name", width: 160 },
    { title: t("system.plugins.table.version"), dataIndex: "version", key: "version", width: 100 },
    {
      title: t("system.plugins.table.status"), dataIndex: "status", key: "status", width: 100,
      render: (v: string) => <Tag color={v === "active" ? "green" : "default"}>{v}</Tag>,
    },
    {
      title: t("system.plugins.table.sha256"), dataIndex: "sha256", key: "sha256", ellipsis: true,
      render: (v: string | null) => v ? <Typography.Text style={{ fontSize: 11 }} copyable>{v}</Typography.Text> : "—",
    },
    {
      title: t("system.plugins.table.installedAt"), dataIndex: "installed_at", key: "installed_at", width: 180,
      render: (v: string) => (v ? new Date(v).toLocaleString("zh-CN") : "—"),
    },
  ];

  return (
    <Table<PluginInfo>
      columns={columns}
      dataSource={plugins ?? []}
      rowKey="name"
      loading={isLoading}
      size="small"
      pagination={false}
      locale={{ emptyText: t("system.plugins.empty") }}
    />
  );
}
