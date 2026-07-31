import { Select, Spin, Typography, notification, theme } from "antd";
import {
  useAgentConfig,
  useConfigs,
  useUpdateAgentConfig,
} from "../../hooks/useConfig";
import { useT } from "../../i18n";
import PageQueryError from "../states/PageQueryError";

/** 「内部任务槽位」区块：按任务类型指派 LLM 配置；留空使用全局激活配置。 */

const SLOT_IDS = ["court", "memory", "synthesis", "edict_parse"] as const;

export default function TaskSlotsSection() {
  const t = useT();
  const { token } = theme.useToken();
  const agentConfigQuery = useAgentConfig();
  const configsQuery = useConfigs();
  const { data: agentConfig } = agentConfigQuery;
  const { data: configsData } = configsQuery;
  const updateMutation = useUpdateAgentConfig();

  const configOptions = (configsData?.configs ?? []).map((c) => ({
    value: c.name,
    label: `${c.name} (${c.model})`,
  }));
  const slots = agentConfig?.task_slots ?? {};

  const handleChange = (slot: string, value: string | undefined) => {
    const next: Record<string, string> = { ...slots };
    if (value) {
      next[slot] = value;
    } else {
      delete next[slot];
    }
    updateMutation.mutate(
      { task_slots: next },
      {
        onSuccess: () =>
          notification.success({
            message: t("system.providers.slots.updated"),
          }),
      },
    );
  };

  const queryError = agentConfigQuery.error ?? configsQuery.error;
  if (queryError) {
    return (
      <PageQueryError
        error={queryError}
        onRetry={() => {
          void agentConfigQuery.refetch();
          void configsQuery.refetch();
        }}
      />
    );
  }

  if (agentConfigQuery.isLoading || configsQuery.isLoading) {
    return <Spin />;
  }

  return (
    <>
      <Typography.Title level={5} style={{ marginBottom: 4 }}>
        {t("system.providers.slots.title")}
      </Typography.Title>
      <div
        style={{
          fontSize: 12,
          color: token.colorTextTertiary,
          marginBottom: 12,
        }}
      >
        {t("system.providers.slots.desc")}
      </div>
      {SLOT_IDS.map((slot) => (
        <div
          key={slot}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: 8,
          }}
        >
          <span style={{ width: 100, fontSize: 13 }}>
            {t(`system.providers.slots.${slot}`)}
          </span>
          <Select
            size="small"
            style={{ width: 280 }}
            allowClear
            placeholder={t("system.providers.slots.placeholder")}
            options={configOptions}
            value={slots[slot] || undefined}
            onChange={(v) => handleChange(slot, v)}
          />
        </div>
      ))}
    </>
  );
}
