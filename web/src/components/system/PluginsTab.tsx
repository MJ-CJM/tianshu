import { Alert, Space, Table, Tag, Typography } from "antd";
import { usePlugins } from "../../hooks/useProviders";
import type { PluginInfo } from "../../api/types";
import { useT } from "../../i18n";
import PageQueryError from "../states/PageQueryError";

export default function PluginsTab() {
  const t = useT();
  const pluginsQuery = usePlugins();
  const { data: plugins, isLoading } = pluginsQuery;

  if (pluginsQuery.error) {
    return (
      <PageQueryError
        error={pluginsQuery.error}
        onRetry={() => void pluginsQuery.refetch()}
      />
    );
  }

  const columns: import("antd/es/table").ColumnsType<PluginInfo> = [
    { title: t("system.plugins.table.name"), dataIndex: "name", key: "name", width: 160 },
    { title: t("system.plugins.table.version"), dataIndex: "version", key: "version", width: 100 },
    {
      title: t("system.plugins.table.status"), dataIndex: "status", key: "status", width: 100,
      render: () => <Tag>{t("system.plugins.manifestOnly")}</Tag>,
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
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert type="info" showIcon message={t("system.plugins.notice")} />
      <Table<PluginInfo>
        columns={columns}
        dataSource={plugins ?? []}
        rowKey="name"
        loading={isLoading}
        size="small"
        pagination={false}
        locale={{ emptyText: t("system.plugins.empty") }}
      />
    </Space>
  );
}
