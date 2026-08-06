import { useState } from "react";
import {
  Table,
  Tag,
  Switch,
  Space,
  Typography,
  Alert,
  notification,
} from "antd";
import { useTools, useSetToolEnabled } from "../../hooks/useSystem";
import type { ToolInfo } from "../../api/types";
import { useT } from "../../i18n";
import { monoStyle } from "./shared";
import { isApiProblem } from "../../api/client";
import PageQueryError from "../states/PageQueryError";

export default function ToolsTab() {
  const t = useT();
  const toolsQuery = useTools();
  const { data: tools, isLoading } = toolsQuery;
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const setEnabledMutation = useSetToolEnabled();

  if (toolsQuery.error) {
    return (
      <PageQueryError
        error={toolsQuery.error}
        onRetry={() => void toolsQuery.refetch()}
      />
    );
  }

  const handleToggle = (name: string, enabled: boolean) => {
    setEnabledMutation.mutate(
      { name, enabled },
      {
        onSuccess: () => {
          notification.success({
            message: enabled
              ? t("system.toast.toolEnabled", { name })
              : t("system.toast.toolDisabled", { name }),
          });
        },
        onError: (err: unknown) => {
          notification.error({
            message: t("system.toast.actionFailed"),
            description: isApiProblem(err)
              ? err.message
              : err instanceof Error
                ? err.message
                : String(err),
          });
        },
      },
    );
  };

  const tierConfig: Record<number, { color: string; label: string }> = {
    0: { color: "green", label: "T0" },
    1: { color: "blue", label: "T1" },
    2: { color: "orange", label: "T2" },
    3: { color: "red", label: "T3" },
  };

  const columns = [
    {
      title: t("system.tools.table.enabled"),
      dataIndex: "enabled",
      key: "enabled",
      width: 80,
      render: (enabled: boolean, record: ToolInfo) => (
        <Switch
          size="small"
          checked={enabled}
          loading={setEnabledMutation.isPending}
          onChange={(next) => handleToggle(record.name, next)}
        />
      ),
    },
    {
      title: t("system.tools.table.name"),
      dataIndex: "name",
      key: "name",
      width: 180,
      render: (name: string) => (
        <Typography.Text strong>{name}</Typography.Text>
      ),
    },
    {
      title: t("system.tools.table.description"),
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: t("system.tools.table.tier"),
      dataIndex: "tier",
      key: "tier",
      width: 70,
      render: (tier: number) => {
        const cfg = tierConfig[tier] ?? { color: "default", label: `T${tier}` };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: t("system.tools.table.personas"),
      dataIndex: "personas",
      key: "personas",
      width: 240,
      render: (personas: string[]) => (
        <Space size={4} wrap>
          {personas.map((p) => (
            <Tag key={p}>{p}</Tag>
          ))}
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Alert type="info" showIcon message={t("system.tools.liveAlert")} />
      <Table
        dataSource={tools}
        columns={columns}
        rowKey="name"
        loading={isLoading}
        size="small"
        pagination={false}
        expandable={{
          expandedRowKeys: expandedKeys,
          onExpandedRowsChange: (keys) => setExpandedKeys(keys as string[]),
          expandedRowRender: (record: ToolInfo) => (
            <pre
              style={{
                ...monoStyle,
                margin: 0,
                padding: 12,
                maxHeight: 300,
                overflow: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {JSON.stringify(record.parameters, null, 2)}
            </pre>
          ),
        }}
      />
    </Space>
  );
}
