import { useState, useEffect, useCallback } from "react";
import {
  Input,
  InputNumber,
  Switch,
  Button,
  Popconfirm,
  Select,
  Table,
  Tag,
  Tooltip,
  Typography,
  Divider,
  Collapse,
  Spin,
  Modal,
  Form,
  notification,
  theme,
} from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { useProviders, useDeleteProvider } from "../../hooks/useProviders";
import {
  useConfigs,
  useCreateConfig,
  useUpdateNamedConfig,
  useDeleteConfig,
  useActivateConfig,
} from "../../hooks/useConfig";
import { useModelProviders } from "../../hooks/useModelProviders";
import type {
  LLMConfig,
  LLMConfigCreateRequest,
  LLMConfigUpdateRequest,
  ProviderInfo,
} from "../../api/types";
import ModelProvidersSection from "./ModelProvidersSection";
import ModelSelect from "./ModelSelect";
import TaskSlotsSection from "./TaskSlotsSection";
import { useT } from "../../i18n";

interface ConfigFormState extends LLMConfigUpdateRequest {
  api_key?: string;
}

function configToForm(c: LLMConfig): ConfigFormState {
  return {
    model: c.model,
    api_base: c.api_base,
    max_retries: c.max_retries,
    temperature: c.temperature,
    top_p: c.top_p,
    max_tokens: c.max_tokens,
    enabled: c.enabled,
  };
}

function ConfigPanelBody({
  config,
  form,
  isActive,
  canDelete,
  token,
  onFieldChange,
  onApply,
  onActivate,
  onDelete,
  applyLoading,
  activateLoading,
}: {
  config: LLMConfig;
  form: ConfigFormState;
  isActive: boolean;
  canDelete: boolean;
  token: ReturnType<typeof theme.useToken>["token"];
  onFieldChange: (field: string, value: unknown) => void;
  onApply: () => void;
  onActivate: () => void;
  onDelete: () => void;
  applyLoading: boolean;
  activateLoading: boolean;
}) {
  const t = useT();
  const labelStyle = {
    marginBottom: 4,
    fontSize: 13,
    color: token.colorTextTertiary,
  };

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <div style={labelStyle}>Model</div>
        {config.provider_id ? (
          <ModelSelect
            providerId={config.provider_id}
            size="small"
            value={form.model ?? ""}
            onChange={(v) => onFieldChange("model", v)}
          />
        ) : (
          <Input
            size="small"
            value={form.model ?? ""}
            onChange={(e) => onFieldChange("model", e.target.value)}
          />
        )}
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={labelStyle}>API Key ({config.api_key_masked})</div>
        <Input.Password
          size="small"
          placeholder={t("system.providers.form.newKeyPlaceholder")}
          value={form.api_key ?? ""}
          onChange={(e) => onFieldChange("api_key", e.target.value)}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={labelStyle}>API Base</div>
        <Input
          size="small"
          placeholder={t("system.providers.form.apiBaseExample")}
          value={form.api_base ?? ""}
          onChange={(e) => onFieldChange("api_base", e.target.value)}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={labelStyle}>Max Retries</div>
        <InputNumber
          size="small"
          min={0}
          max={10}
          value={form.max_retries}
          onChange={(v) => onFieldChange("max_retries", v ?? 0)}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={labelStyle}>Temperature</div>
        <InputNumber
          size="small"
          min={0}
          max={2}
          step={0.1}
          value={form.temperature}
          onChange={(v) => onFieldChange("temperature", v ?? 0.7)}
          style={{ width: 100 }}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={labelStyle}>Top P</div>
        <InputNumber
          size="small"
          min={0}
          max={1}
          step={0.1}
          value={form.top_p}
          onChange={(v) => onFieldChange("top_p", v ?? 1.0)}
          style={{ width: 100 }}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={labelStyle}>Max Tokens</div>
        <InputNumber
          size="small"
          min={1}
          max={128000}
          value={form.max_tokens}
          onChange={(v) => onFieldChange("max_tokens", v ?? 4096)}
          style={{ width: 140 }}
        />
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={labelStyle}>Enabled</div>
        <Switch
          size="small"
          checked={form.enabled}
          onChange={(v) => onFieldChange("enabled", v)}
        />
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div style={{ display: "flex", gap: 8 }}>
          <Button size="small" type="primary" loading={applyLoading} onClick={onApply}>
            {t("system.globalConfig.apply")}
          </Button>
          {!isActive && (
            <Button size="small" loading={activateLoading} onClick={onActivate}>
              {t("system.providers.activate")}
            </Button>
          )}
        </div>
        {canDelete && (
          <Popconfirm
            title={t("system.providers.confirmDeleteConfig")}
            onConfirm={onDelete}
            okText={t("action.delete")}
            cancelText={t("common.cancel")}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              {t("action.delete")}
            </Button>
          </Popconfirm>
        )}
      </div>
    </div>
  );
}

export default function ProvidersTab() {
  const t = useT();
  const { token } = theme.useToken();
  const { data: providers, isLoading: providersLoading } = useProviders();
  const deleteProviderMutation = useDeleteProvider();

  // LLM Config state
  const { data: configsData, isLoading: configsLoading } = useConfigs();
  const createMutation = useCreateConfig();
  const updateMutation = useUpdateNamedConfig();
  const deleteMutation = useDeleteConfig();
  const activateMutation = useActivateConfig();
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [addForm] = Form.useForm<LLMConfigCreateRequest>();
  const addProviderId = Form.useWatch("provider_id", addForm);
  const { data: modelProviders } = useModelProviders();
  const [forms, setForms] = useState<Record<string, ConfigFormState>>({});

  useEffect(() => {
    if (configsData?.configs) {
      setForms((prev) => {
        const next: Record<string, ConfigFormState> = {};
        for (const c of configsData.configs) {
          next[c.name] = prev[c.name] ?? configToForm(c);
        }
        return next;
      });
    }
  }, [configsData]);

  const updateField = useCallback(
    (name: string, field: string, value: unknown) => {
      setForms((prev) => ({
        ...prev,
        [name]: { ...prev[name], [field]: value },
      }));
    },
    [],
  );

  const handleApply = useCallback(
    (name: string) => {
      const form = forms[name];
      const config = configsData?.configs.find((c) => c.name === name);
      if (!form || !config) return;

      const payload: LLMConfigUpdateRequest = {};
      if (form.model !== undefined && form.model !== config.model)
        payload.model = form.model;
      if (form.api_base !== undefined && form.api_base !== config.api_base)
        payload.api_base = form.api_base;
      if (form.max_retries !== undefined && form.max_retries !== config.max_retries)
        payload.max_retries = form.max_retries;
      if (form.temperature !== undefined && form.temperature !== config.temperature)
        payload.temperature = form.temperature;
      if (form.top_p !== undefined && form.top_p !== config.top_p)
        payload.top_p = form.top_p;
      if (form.max_tokens !== undefined && form.max_tokens !== config.max_tokens)
        payload.max_tokens = form.max_tokens;
      if (form.enabled !== undefined && form.enabled !== config.enabled)
        payload.enabled = form.enabled;
      if (form.api_key) payload.api_key = form.api_key;

      if (Object.keys(payload).length === 0) {
        notification.info({ message: t("system.toast.noChanges") });
        return;
      }
      updateMutation.mutate(
        { name, req: payload },
        {
          onSuccess: () => {
            notification.success({ message: t("system.toast.llmConfigUpdated") });
            setForms((prev) => ({
              ...prev,
              [name]: { ...prev[name], api_key: undefined },
            }));
          },
        },
      );
    },
    [forms, configsData, updateMutation, t],
  );

  const handleAdd = useCallback(() => {
    addForm.validateFields().then((values) => {
      createMutation.mutate(values, {
        onSuccess: () => {
          notification.success({ message: t("system.toast.llmConfigAdded", { name: values.name }) });
          setAddModalOpen(false);
          addForm.resetFields();
        },
        onError: (err: unknown) => {
          const msg = err instanceof Error ? err.message : t("system.toast.llmConfigDuplicate");
          notification.error({ message: msg });
        },
      });
    });
  }, [addForm, createMutation, t]);

  const handleDeleteProvider = (name: string) => {
    deleteProviderMutation.mutate(name, {
      onSuccess: () => notification.success({ message: t("system.toast.providerDeleted", { name }) }),
    });
  };

  const configs = configsData?.configs ?? [];
  const activeName = configsData?.active_name ?? "";

  const providerColumns: import("antd/es/table").ColumnsType<ProviderInfo> = [
    { title: t("system.providers.table.name"), dataIndex: "name", key: "name", width: 140 },
    { title: t("system.providers.table.model"), dataIndex: "model", key: "model", width: 160 },
    {
      title: t("system.providers.table.status"), dataIndex: "status", key: "status", width: 100,
      render: (v: string) => {
        const color = v === "active" ? "green" : v === "degraded" ? "orange" : "red";
        return <Tag color={color}>{v}</Tag>;
      },
    },
    { title: t("system.providers.table.priority"), dataIndex: "priority", key: "priority", width: 80, align: "right" },
    {
      title: t("system.providers.table.rpm"), dataIndex: "rpm_limit", key: "rpm_limit", width: 80, align: "right",
      render: (v: number | null) => v ?? "—",
    },
    {
      title: t("system.providers.table.cost"), key: "cost", width: 200, align: "right",
      render: (_, r) => {
        const eff = r.pricing_effective;
        if (eff?.billing === "subscription") {
          return <Tag color="purple">{t("system.providers.costSubscription")}</Tag>;
        }
        const miss = eff ? eff.miss : r.cost_per_1k_prompt;
        const hit = eff ? eff.hit : r.cost_per_1k_cache_read;
        const out = eff ? eff.out : r.cost_per_1k_completion;
        const source = eff?.source ?? "default";
        const tooltip =
          source === "custom"
            ? t("system.providers.costAllCustom")
            : source === "mixed"
            ? t("system.providers.costPartial")
            : t("system.providers.costNoCustom");
        const fmt = (v: number | null) => (v != null ? v.toFixed(5).replace(/0+$/, "").replace(/\.$/, "") : "—");
        return (
          <Tooltip title={tooltip}>
            <span style={{ fontSize: 12, fontFamily: "monospace" }}>
              {fmt(miss)} / {fmt(hit)} / {fmt(out)}
            </span>
          </Tooltip>
        );
      },
    },
    {
      title: "", key: "actions", width: 50,
      render: (_, record) => (
        <Popconfirm title={t("system.providers.confirmDeleteProvider")} onConfirm={() => handleDeleteProvider(record.name)}>
          <Button type="text" danger size="small" icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  const collapseItems = configs.map((c) => ({
    key: c.name,
    label: (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          width: "100%",
        }}
      >
        <span style={{ fontWeight: 500 }}>
          {c.name}
          {c.name === activeName && (
            <Tag color="green" style={{ marginLeft: 8, fontSize: 11 }}>
              {t("system.providers.active")}
            </Tag>
          )}
          {c.provider_id && (
            <Tag color="blue" style={{ marginLeft: 8, fontSize: 11 }}>
              {c.provider_id}
            </Tag>
          )}
        </span>
        <span style={{ fontSize: 12, color: token.colorTextTertiary }}>
          {c.model}
        </span>
      </div>
    ),
    children: (
      <ConfigPanelBody
        config={c}
        form={forms[c.name] ?? configToForm(c)}
        isActive={c.name === activeName}
        canDelete={configs.length > 1 && c.name !== activeName}
        token={token}
        onFieldChange={(field, value) => updateField(c.name, field, value)}
        onApply={() => handleApply(c.name)}
        onActivate={() =>
          activateMutation.mutate(c.name, {
            onSuccess: () =>
              notification.success({ message: t("system.toast.llmConfigSwitched", { name: c.name }) }),
          })
        }
        onDelete={() =>
          deleteMutation.mutate(c.name, {
            onSuccess: () =>
              notification.success({ message: t("system.toast.llmConfigDeleted", { name: c.name }) }),
          })
        }
        applyLoading={updateMutation.isPending}
        activateLoading={activateMutation.isPending}
      />
    ),
  }));

  return (
    <>
      {/* 模型供应商注册表 */}
      <ModelProvidersSection />

      <Divider />

      {/* Provider 列表 */}
      <Typography.Title level={5} style={{ marginBottom: 12 }}>{t("system.providers.listTitle")}</Typography.Title>
      <Table<ProviderInfo>
        columns={providerColumns}
        dataSource={providers ?? []}
        rowKey="name"
        loading={providersLoading}
        size="small"
        pagination={false}
        locale={{ emptyText: t("system.providers.empty") }}
      />

      <Divider />

      {/* LLM 配置管理 */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <Typography.Title level={5} style={{ margin: 0 }}>{t("system.providers.llmConfigTitle")}</Typography.Title>
        <Button
          type="primary"
          size="small"
          icon={<PlusOutlined />}
          onClick={() => setAddModalOpen(true)}
        >
          {t("system.providers.addConfig")}
        </Button>
      </div>

      {configsLoading ? (
        <Spin />
      ) : (
        <Collapse
          accordion
          items={collapseItems}
          style={{ background: "transparent" }}
        />
      )}

      <Divider />

      {/* 内部任务槽位 */}
      <TaskSlotsSection />

      {/* Add Config Modal */}
      <Modal
        title={t("system.providers.addConfigTitle")}
        open={addModalOpen}
        onOk={handleAdd}
        onCancel={() => {
          setAddModalOpen(false);
          addForm.resetFields();
        }}
        confirmLoading={createMutation.isPending}
        okText={t("action.add")}
        cancelText={t("common.cancel")}
      >
        <Form form={addForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="name"
            label={t("system.providers.form.name")}
            rules={[{ required: true, message: t("system.providers.form.nameRequired") }]}
          >
            <Input placeholder={t("system.providers.form.namePlaceholder")} />
          </Form.Item>
          <Form.Item
            name="provider_id"
            label={t("system.providers.registry.providerSelect")}
          >
            <Select
              allowClear
              placeholder={t("system.providers.registry.providerSelectPlaceholder")}
              options={(modelProviders ?? []).map((p) => ({
                value: p.id,
                label: `${p.display_name} (${p.id})`,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="model"
            label={t("system.providers.form.model")}
            rules={[{ required: true, message: t("system.providers.form.modelRequired") }]}
          >
            {addProviderId ? (
              <ModelSelect providerId={addProviderId} />
            ) : (
              <Input placeholder={t("system.providers.form.modelPlaceholder")} />
            )}
          </Form.Item>
          <Form.Item name="api_key" label="API Key">
            <Input.Password
              placeholder={
                addProviderId
                  ? t("system.providers.registry.providerKeyPlaceholder")
                  : t("system.providers.form.apiKeyPlaceholder")
              }
            />
          </Form.Item>
          {!addProviderId && (
            <Form.Item name="api_base" label="API Base" preserve={false}>
              <Input placeholder={t("system.providers.form.apiBasePlaceholder")} />
            </Form.Item>
          )}
          <Form.Item name="max_retries" label="Max Retries" initialValue={3}>
            <InputNumber min={0} max={10} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="temperature" label="Temperature" initialValue={0.7}>
            <InputNumber min={0} max={2} step={0.1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="top_p" label="Top P" initialValue={1.0}>
            <InputNumber min={0} max={1} step={0.1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="max_tokens" label="Max Tokens" initialValue={4096}>
            <InputNumber min={1} max={128000} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
