import { useEffect, useState } from "react";
import {
  Card,
  Table,
  Tag,
  Switch,
  Button,
  Space,
  Modal,
  Form,
  Input,
  InputNumber,
  Select,
  Popconfirm,
  notification,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listInstances,
  listPersonas,
  createInstance,
  updateInstance,
  setInstanceEnabled,
  deleteInstance,
  type InstanceView,
  type PersonaSummary,
} from "../../api/tongzheng";
import { useT, type TFunction } from "../../i18n";

type ChannelType = "feishu" | "telegram";

// 各渠道的「配置字段白名单」（不含 secret / label / enabled）。
const FEISHU_CONFIG_FIELDS = [
  "app_id",
  "domain",
  "connection_mode",
  "allowed_users",
  "home_channel",
  "encrypt_key",
  "verification_token",
  "bot_open_id",
  "bot_name",
  "webhook_path",
  "ws_reconnect_interval",
  "text_batch_delay",
  "dedup_cache_size",
  "assistant_persona_id",
  "intent_llm_enabled",
  "enable_edict_submission",
] as const;

const TELEGRAM_CONFIG_FIELDS = [
  "connection_mode",
  "allowed_users",
  "home_channel",
  "webhook_path",
  "webhook_secret",
  "poll_timeout",
  "text_batch_delay",
  "dedup_cache_size",
  "assistant_persona_id",
  "enable_edict_submission",
] as const;

const FEISHU_DEFAULTS = {
  domain: "feishu",
  connection_mode: "websocket",
  webhook_path: "/feishu/webhook",
  ws_reconnect_interval: 120,
  text_batch_delay: 0.6,
  dedup_cache_size: 2048,
  assistant_persona_id: "tongzheng",
  intent_llm_enabled: true,
  enable_edict_submission: false,
};

const TELEGRAM_DEFAULTS = {
  connection_mode: "polling",
  webhook_path: "/telegram/webhook",
  poll_timeout: 30,
  text_batch_delay: 0.6,
  dedup_cache_size: 2048,
  assistant_persona_id: "tongzheng",
  enable_edict_submission: false,
};

function isDefaultInstance(id: string): boolean {
  return id.endsWith("-default");
}

function notifyError(t: TFunction, err: unknown) {
  const e = err as { response?: { data?: { detail?: string } }; message?: string };
  notification.error({
    message: t("tongzheng.toast.saveFailed"),
    description: e?.response?.data?.detail ?? e?.message ?? String(err),
  });
}

function notifySaved(t: TFunction, result: { reloaded: boolean; reason: string }) {
  notification.success({
    message: t("tongzheng.toast.saved"),
    description: result.reloaded
      ? t("tongzheng.toast.savedReloaded")
      : t("tongzheng.toast.savedNoReload", { reason: result.reason ?? "" }),
    duration: 5,
  });
}

// ===================== 表单（新增/编辑共用）=====================

interface FormValues {
  label?: string;
  enabled: boolean;
  secret?: string;
  [key: string]: unknown;
}

interface InstanceFormProps {
  open: boolean;
  channelType: ChannelType;
  // editing 为 null 时是「新增」，否则是「编辑」既有实例
  editing: InstanceView | null;
  personas: PersonaSummary[];
  onClose: () => void;
}

function InstanceForm({
  open,
  channelType,
  editing,
  personas,
  onClose,
}: InstanceFormProps) {
  const t = useT();
  const qc = useQueryClient();
  const [form] = Form.useForm<FormValues>();
  const [secretChanged, setSecretChanged] = useState(false);

  const fields =
    channelType === "feishu" ? FEISHU_CONFIG_FIELDS : TELEGRAM_CONFIG_FIELDS;
  const defaults =
    channelType === "feishu" ? FEISHU_DEFAULTS : TELEGRAM_DEFAULTS;

  useEffect(() => {
    if (!open) return;
    if (editing) {
      const configValues: Record<string, unknown> = {};
      for (const f of fields) configValues[f] = editing[f];
      form.setFieldsValue({
        label: editing.label,
        enabled: editing.enabled,
        secret: "", // 默认空，不显示掩码
        ...configValues,
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ enabled: true, label: "", secret: "", ...defaults });
    }
    setSecretChanged(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editing]);

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const config: Record<string, unknown> = {};
      for (const f of fields) config[f] = values[f];
      if (editing) {
        return await updateInstance(editing.instance_id, {
          label: values.label,
          enabled: values.enabled,
          config,
          secret: secretChanged ? values.secret : undefined,
        });
      }
      const result = await createInstance({
        channel_type: channelType,
        label: values.label,
        enabled: values.enabled,
        config,
        secret: secretChanged ? values.secret : undefined,
      });
      return { reloaded: result.reloaded, reason: result.reason };
    },
    onSuccess: (result) => {
      notifySaved(t, result);
      qc.invalidateQueries({ queryKey: ["tongzheng"] });
      onClose();
    },
    onError: (err: unknown) => notifyError(t, err),
  });

  const hasSecret = editing?._has_secret ?? false;
  const secretPlaceholder =
    channelType === "feishu"
      ? hasSecret
        ? t("tongzheng.placeholder.appSecretConfigured")
        : t("tongzheng.placeholder.appSecretMissing")
      : hasSecret
        ? t("tongzheng.tg.placeholder.botTokenConfigured")
        : t("tongzheng.tg.placeholder.botTokenMissing");
  const secretLabel =
    channelType === "feishu"
      ? t("tongzheng.field.appSecret")
      : t("tongzheng.tg.field.botToken");

  const personaOptions = personas.map((p) => ({
    value: p.id,
    label: `${p.name} (${p.department})`,
  }));

  const titleKey = editing
    ? "tongzheng.instances.editTitle"
    : channelType === "feishu"
      ? "tongzheng.instances.addFeishu"
      : "tongzheng.instances.addTelegram";

  return (
    <Modal
      open={open}
      title={t(titleKey)}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText={t("tongzheng.action.save")}
      confirmLoading={mutation.isPending}
      destroyOnClose
      width={640}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={(values) => mutation.mutate(values)}
      >
        <Form.Item label={t("tongzheng.instances.fieldLabel")} name="label">
          <Input placeholder={t("tongzheng.instances.fieldLabelPlaceholder")} />
        </Form.Item>

        <Form.Item
          label={t("tongzheng.instances.fieldEnabled")}
          name="enabled"
          valuePropName="checked"
        >
          <Switch />
        </Form.Item>

        <Form.Item
          label={`${secretLabel} ${hasSecret ? t("tongzheng.instances.secretConfigured") : ""}`}
          name="secret"
          extra={
            channelType === "feishu"
              ? t("tongzheng.field.appSecretExtra")
              : t("tongzheng.tg.field.botTokenExtra")
          }
        >
          <Input.Password
            placeholder={secretPlaceholder}
            onChange={() => setSecretChanged(true)}
          />
        </Form.Item>

        {channelType === "feishu" ? (
          <FeishuFields t={t} personaOptions={personaOptions} />
        ) : (
          <TelegramFields t={t} personaOptions={personaOptions} />
        )}
      </Form>
    </Modal>
  );
}

interface FieldsProps {
  t: TFunction;
  personaOptions: { value: string; label: string }[];
}

function FeishuFields({ t, personaOptions }: FieldsProps) {
  return (
    <>
      <Form.Item
        label={t("tongzheng.field.appId")}
        name="app_id"
        rules={[{ required: true }]}
      >
        <Input placeholder="cli_xxxxxxxxx" />
      </Form.Item>
      <Form.Item label={t("tongzheng.field.domain")} name="domain">
        <Select
          options={[
            { value: "feishu", label: t("tongzheng.option.domainFeishu") },
            { value: "lark", label: t("tongzheng.option.domainLark") },
          ]}
        />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.connectionMode")}
        name="connection_mode"
      >
        <Select
          options={[
            { value: "websocket", label: t("tongzheng.option.modeWebsocket") },
            { value: "webhook", label: t("tongzheng.option.modeWebhook") },
          ]}
        />
      </Form.Item>
      <Form.Item label={t("tongzheng.field.webhookPath")} name="webhook_path">
        <Input placeholder="/feishu/webhook" />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.allowedUsers")}
        name="allowed_users"
        extra={t("tongzheng.field.allowedUsersExtra")}
      >
        <Input placeholder={t("tongzheng.placeholder.allowedUsers")} />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.homeChannel")}
        name="home_channel"
        extra={t("tongzheng.field.homeChannelExtra")}
      >
        <Input placeholder={t("tongzheng.placeholder.homeChannel")} />
      </Form.Item>
      <Form.Item label={t("tongzheng.field.botOpenId")} name="bot_open_id">
        <Input placeholder={t("tongzheng.placeholder.botOpenId")} />
      </Form.Item>
      <Form.Item label={t("tongzheng.instances.botName")} name="bot_name">
        <Input placeholder={t("tongzheng.instances.botNamePlaceholder")} />
      </Form.Item>
      <Form.Item label={t("tongzheng.field.encryptKey")} name="encrypt_key">
        <Input.Password placeholder={t("tongzheng.placeholder.encryptKey")} />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.verificationToken")}
        name="verification_token"
      >
        <Input.Password
          placeholder={t("tongzheng.placeholder.verificationToken")}
        />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.wsReconnect")}
        name="ws_reconnect_interval"
      >
        <InputNumber min={30} max={600} style={{ width: "100%" }} />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.textBatchDelay")}
        name="text_batch_delay"
      >
        <InputNumber min={0} max={5} step={0.1} style={{ width: "100%" }} />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.dedupCacheSize")}
        name="dedup_cache_size"
      >
        <InputNumber min={128} max={65536} style={{ width: "100%" }} />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.assistantPersona")}
        name="assistant_persona_id"
        extra={t("tongzheng.field.assistantPersonaExtra")}
      >
        <Select
          placeholder={t("tongzheng.placeholder.assistantPersona")}
          options={personaOptions}
        />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.instances.intentLlm")}
        name="intent_llm_enabled"
        valuePropName="checked"
      >
        <Switch />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.enableEdict")}
        name="enable_edict_submission"
        valuePropName="checked"
        extra={t("tongzheng.field.enableEdictExtra")}
      >
        <Switch />
      </Form.Item>
    </>
  );
}

function TelegramFields({ t, personaOptions }: FieldsProps) {
  return (
    <>
      <Form.Item
        label={t("tongzheng.tg.field.connectionMode")}
        name="connection_mode"
      >
        <Select
          options={[
            { value: "polling", label: t("tongzheng.tg.option.modePolling") },
            { value: "webhook", label: t("tongzheng.tg.option.modeWebhook") },
          ]}
        />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.tg.field.allowedUsers")}
        name="allowed_users"
        extra={t("tongzheng.tg.field.allowedUsersExtra")}
      >
        <Input placeholder={t("tongzheng.tg.placeholder.allowedUsers")} />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.tg.field.homeChannel")}
        name="home_channel"
        extra={t("tongzheng.tg.field.homeChannelExtra")}
      >
        <Input placeholder={t("tongzheng.tg.placeholder.homeChannel")} />
      </Form.Item>
      <Form.Item label={t("tongzheng.tg.field.webhookPath")} name="webhook_path">
        <Input placeholder="/telegram/webhook" />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.tg.field.webhookSecret")}
        name="webhook_secret"
      >
        <Input.Password placeholder={t("tongzheng.tg.placeholder.webhookSecret")} />
      </Form.Item>
      <Form.Item label={t("tongzheng.tg.field.pollTimeout")} name="poll_timeout">
        <InputNumber min={1} max={120} style={{ width: "100%" }} />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.tg.field.textBatchDelay")}
        name="text_batch_delay"
      >
        <InputNumber min={0} max={5} step={0.1} style={{ width: "100%" }} />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.tg.field.dedupCacheSize")}
        name="dedup_cache_size"
      >
        <InputNumber min={128} max={65536} style={{ width: "100%" }} />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.assistantPersona")}
        name="assistant_persona_id"
        extra={t("tongzheng.field.assistantPersonaExtra")}
      >
        <Select
          placeholder={t("tongzheng.placeholder.assistantPersona")}
          options={personaOptions}
        />
      </Form.Item>
      <Form.Item
        label={t("tongzheng.field.enableEdict")}
        name="enable_edict_submission"
        valuePropName="checked"
        extra={t("tongzheng.field.enableEdictExtra")}
      >
        <Switch />
      </Form.Item>
    </>
  );
}

// ===================== 列表 =====================

export default function InstanceManager() {
  const t = useT();
  const qc = useQueryClient();
  const [modal, setModal] = useState<{
    open: boolean;
    channelType: ChannelType;
    editing: InstanceView | null;
  }>({ open: false, channelType: "feishu", editing: null });

  const { data: instances, isLoading } = useQuery({
    queryKey: ["tongzheng", "instances"],
    queryFn: listInstances,
    refetchInterval: 15_000,
  });

  const { data: personas } = useQuery({
    queryKey: ["tongzheng", "personas"],
    queryFn: listPersonas,
  });

  const enabledMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      setInstanceEnabled(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tongzheng"] }),
    onError: (err: unknown) => notifyError(t, err),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteInstance(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tongzheng"] }),
    onError: (err: unknown) => notifyError(t, err),
  });

  const sorted = [...(instances ?? [])].sort((a, b) => {
    if (a.channel_type !== b.channel_type)
      return a.channel_type < b.channel_type ? -1 : 1;
    return a.instance_id < b.instance_id ? -1 : 1;
  });

  const columns: ColumnsType<InstanceView> = [
    {
      title: t("tongzheng.instances.colLabel"),
      dataIndex: "label",
      key: "label",
      render: (label: string, row) => (
        <Space>
          <span>{label || row.instance_id}</span>
          {isDefaultInstance(row.instance_id) && (
            <Tag color="blue">{t("tongzheng.instances.defaultTag")}</Tag>
          )}
        </Space>
      ),
    },
    {
      title: t("tongzheng.instances.colChannel"),
      dataIndex: "channel_type",
      key: "channel_type",
      render: (ch: ChannelType) => (
        <Tag>{ch === "feishu" ? "飞书 Feishu" : "Telegram"}</Tag>
      ),
    },
    {
      title: t("tongzheng.instances.colRunning"),
      key: "running",
      render: (_, row) =>
        row.running ? (
          <Tag color="green">
            {t("tongzheng.status.running", { mode: row.mode ?? "" })}
          </Tag>
        ) : (
          <Tag>{t("tongzheng.instances.stopped")}</Tag>
        ),
    },
    {
      title: t("tongzheng.instances.colEnabled"),
      key: "enabled",
      render: (_, row) => (
        <Switch
          checked={row.enabled}
          loading={
            enabledMutation.isPending &&
            enabledMutation.variables?.id === row.instance_id
          }
          onChange={(checked) =>
            enabledMutation.mutate({ id: row.instance_id, enabled: checked })
          }
        />
      ),
    },
    {
      title: t("tongzheng.instances.colActions"),
      key: "actions",
      render: (_, row) => (
        <Space>
          <Button
            size="small"
            onClick={() =>
              setModal({
                open: true,
                channelType: row.channel_type,
                editing: row,
              })
            }
          >
            {t("tongzheng.instances.edit")}
          </Button>
          <Popconfirm
            title={t("tongzheng.instances.deleteConfirm")}
            onConfirm={() => deleteMutation.mutate(row.instance_id)}
            disabled={isDefaultInstance(row.instance_id)}
          >
            <Button
              size="small"
              danger
              disabled={isDefaultInstance(row.instance_id)}
              loading={
                deleteMutation.isPending &&
                deleteMutation.variables === row.instance_id
              }
            >
              {t("tongzheng.instances.delete")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={t("tongzheng.instances.title")}
      extra={
        <Space>
          <Button
            type="primary"
            onClick={() =>
              setModal({ open: true, channelType: "feishu", editing: null })
            }
          >
            {t("tongzheng.instances.addFeishuBtn")}
          </Button>
          <Button
            type="primary"
            onClick={() =>
              setModal({ open: true, channelType: "telegram", editing: null })
            }
          >
            {t("tongzheng.instances.addTelegramBtn")}
          </Button>
        </Space>
      }
    >
      <Table
        rowKey="instance_id"
        loading={isLoading}
        columns={columns}
        dataSource={sorted}
        pagination={false}
        size="middle"
      />
      <InstanceForm
        open={modal.open}
        channelType={modal.channelType}
        editing={modal.editing}
        personas={personas ?? []}
        onClose={() => setModal((m) => ({ ...m, open: false }))}
      />
    </Card>
  );
}
