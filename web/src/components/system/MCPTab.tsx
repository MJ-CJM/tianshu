import { useState } from "react";
import {
  Card,
  Tag,
  Space,
  Button,
  Switch,
  Empty,
  Spin,
  Typography,
  Alert,
  notification,
  Collapse,
  List,
} from "antd";
import { ReloadOutlined, PlusOutlined } from "@ant-design/icons";
import { useT } from "../../i18n";
import {
  useMCPServers,
  usePatchMCPServer,
  useReloadMCP,
} from "../../hooks/useMCP";
import type { MCPServerInfo } from "../../api/mcp";
import CreateMCPServerModal from "./CreateMCPServerModal";
import PageQueryError from "../states/PageQueryError";

const STATUS_COLOR: Record<string, string> = {
  connected: "green",
  reconnecting: "gold",
  error: "red",
  stopped: "default",
  pending: "blue",
  disabled: "default",
  unknown: "default",
};

function TierTag({ tier }: { tier: number }) {
  const colors = ["green", "blue", "gold", "orange", "red"];
  const labels = ["T0", "T1", "T2", "T3", "T4"];
  const idx = Math.max(0, Math.min(4, tier));
  return <Tag color={colors[idx]}>{labels[idx]}</Tag>;
}

function ServerCard({ server }: { server: MCPServerInfo }) {
  const t = useT();
  const patch = usePatchMCPServer();

  const onToggle = async (next: boolean) => {
    try {
      await patch.mutateAsync({
        name: server.name,
        patch: { enabled: next },
      });
      notification.success({
        message: t("system.mcp.notify.savedTitle"),
        description: t("system.mcp.notify.savedNeedReload"),
      });
    } catch (e) {
      notification.error({
        message: t("system.mcp.notify.errorTitle"),
        description: String(e),
      });
    }
  };

  return (
    <Card
      style={{ marginBottom: 12 }}
      title={
        <Space>
          <Typography.Text strong>{server.name}</Typography.Text>
          <Tag color={STATUS_COLOR[server.status] ?? "default"}>
            {t(`system.mcp.status.${server.status}`)}
          </Tag>
          <Tag>{server.transport}</Tag>
        </Space>
      }
      extra={
        <Space>
          <Switch
            checked={server.enabled}
            onChange={onToggle}
            loading={patch.isPending}
            checkedChildren={t("system.mcp.enabled")}
            unCheckedChildren={t("system.mcp.disabled")}
          />
        </Space>
      }
    >
      {server.last_error && (
        <Alert
          type="error"
          message={t("system.mcp.lastError")}
          description={server.last_error}
          style={{ marginBottom: 12 }}
          showIcon
        />
      )}
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        {server.transport === "stdio" ? (
          <>
            <code>{server.command}</code>{" "}
            <code>{(server.args ?? []).join(" ")}</code>
          </>
        ) : (
          <code>{server.url}</code>
        )}
      </Typography.Paragraph>
      <Space wrap size={[4, 4]} style={{ marginBottom: 8 }}>
        <Tag>
          {t("system.mcp.defaultTier")}: <TierTag tier={server.default_tier} />
        </Tag>
        <Tag>
          {t("system.mcp.timeout")}: {server.timeout}s
        </Tag>
        {(server.env_keys ?? []).length > 0 && (
          <Tag>env: {(server.env_keys ?? []).join(", ")}</Tag>
        )}
        {(server.header_keys ?? []).length > 0 && (
          <Tag>headers: {(server.header_keys ?? []).join(", ")}</Tag>
        )}
      </Space>
      <Collapse
        size="small"
        ghost
        items={[
          {
            key: "tools",
            label: t("system.mcp.discoveredTools", {
              n: (server.tools ?? []).length,
            }),
            children:
              (server.tools ?? []).length === 0 ? (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={t("system.mcp.noTools")}
                />
              ) : (
                <List
                  size="small"
                  dataSource={server.tools ?? []}
                  renderItem={(tool) => (
                    <List.Item>
                      <Space direction="vertical" size={0}>
                        <code>
                          mcp_{server.name}_{tool.name}
                        </code>
                        <Typography.Text type="secondary">
                          {tool.description || "—"}
                        </Typography.Text>
                      </Space>
                    </List.Item>
                  )}
                />
              ),
          },
        ]}
      />
    </Card>
  );
}

export default function MCPTab() {
  const t = useT();
  const serversQuery = useMCPServers();
  const { data: servers, isLoading } = serversQuery;
  const reload = useReloadMCP();
  const [createOpen, setCreateOpen] = useState(false);

  if (serversQuery.error) {
    return (
      <PageQueryError
        error={serversQuery.error}
        onRetry={() => void serversQuery.refetch()}
      />
    );
  }

  if (isLoading) {
    return <Spin />;
  }

  const onReload = async () => {
    try {
      const res = await reload.mutateAsync();
      notification.success({
        message: t("system.mcp.notify.reloadedTitle"),
        description: t("system.mcp.notify.reloadedDesc", {
          servers: res.data?.servers ?? 0,
          active: res.data?.active_sessions ?? 0,
        }),
      });
    } catch (e) {
      notification.error({
        message: t("system.mcp.notify.errorTitle"),
        description: String(e),
      });
    }
  };

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateOpen(true)}
        >
          {t("system.mcp.addServer")}
        </Button>
        <Button
          icon={<ReloadOutlined />}
          onClick={onReload}
          loading={reload.isPending}
        >
          {t("system.mcp.reload")}
        </Button>
        <Typography.Text type="secondary">
          {t("system.mcp.configHint")}
        </Typography.Text>
      </Space>
      {!servers || servers.length === 0 ? (
        <Empty description={t("system.mcp.empty")} />
      ) : (
        servers.map((s) => <ServerCard key={s.name} server={s} />)
      )}
      <CreateMCPServerModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />
    </div>
  );
}
