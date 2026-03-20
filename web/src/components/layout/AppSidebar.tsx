import {
  Layout,
  Menu,
  Button,
  Drawer,
  Input,
  InputNumber,
  Switch,
  Spin,
  Tooltip,
  Segmented,
  Divider,
  Collapse,
  Tag,
  Modal,
  Form,
  Popconfirm,
  notification,
  theme,
} from "antd";
import {
  UnorderedListOutlined,
  PlusCircleOutlined,
  PlusOutlined,
  SettingOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SunOutlined,
  MoonOutlined,
  DeleteOutlined,
  AuditOutlined,
  ScheduleOutlined,
  SafetyCertificateOutlined,
  DollarOutlined,
  ApiOutlined,
  BookOutlined,
  TeamOutlined,
  CrownOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { useNavigate, useLocation } from "react-router-dom";
import { useState, useEffect, useCallback } from "react";
import {
  useAgentConfig,
  useUpdateAgentConfig,
  useConfigs,
  useCreateConfig,
  useUpdateNamedConfig,
  useDeleteConfig,
  useActivateConfig,
} from "../../hooks/useConfig";
import { useTheme } from "../../hooks/useTheme";
import { useNeedsReview } from "../../hooks/useApprovals";
import type {
  AgentConfigUpdateRequest,
  LLMConfig,
  LLMConfigCreateRequest,
  LLMConfigUpdateRequest,
} from "../../api/types";

const staticMenuItems = [
  {
    key: "/",
    icon: <UnorderedListOutlined />,
    label: "敕令总览",
  },
  {
    key: "/edicts/create",
    icon: <PlusCircleOutlined />,
    label: "颁发敕令",
  },
];

interface ConfigFormState extends LLMConfigUpdateRequest {
  api_key?: string;
}

export default function AppSidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { token } = theme.useToken();
  const { mode, toggleTheme } = useTheme();
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [addForm] = Form.useForm<LLMConfigCreateRequest>();

  const { data: reviewData } = useNeedsReview();
  const reviewCount = reviewData?.metadata?.total ?? reviewData?.data?.length ?? 0;

  const menuItems = [
    ...staticMenuItems,
    {
      key: "/approvals",
      icon: <AuditOutlined />,
      label: reviewCount > 0 ? `批红台 (${reviewCount})` : "批红台",
    },
    {
      key: "/scheduler",
      icon: <ScheduleOutlined />,
      label: "文书房",
    },
    {
      key: "/audit",
      icon: <SafetyCertificateOutlined />,
      label: "审计司",
    },
    {
      key: "/cost",
      icon: <DollarOutlined />,
      label: "户部账房",
    },
    {
      key: "/memory",
      icon: <BookOutlined />,
      label: "文渊阁",
    },
    {
      key: "/consultation",
      icon: <TeamOutlined />,
      label: "廷议",
    },
    {
      key: "/personas",
      icon: <CrownOutlined />,
      label: "百官阁",
    },
    {
      key: "/providers",
      icon: <ApiOutlined />,
      label: "Provider管理",
    },
    {
      key: "/system",
      icon: <ToolOutlined />,
      label: "藏兵阁",
    },
  ];

  // Note: DAG battle map is accessible via edict detail "查看作战图" button,
  // not as a direct sidebar item (it requires a dagId parameter).

  const { data: configsData, isLoading } = useConfigs();
  const createMutation = useCreateConfig();
  const updateMutation = useUpdateNamedConfig();
  const deleteMutation = useDeleteConfig();
  const activateMutation = useActivateConfig();

  const { data: agentConfigData } = useAgentConfig();
  const updateAgentMutation = useUpdateAgentConfig();
  const [agentForm, setAgentForm] = useState<AgentConfigUpdateRequest>({});

  // Sync agent form from server data
  useEffect(() => {
    if (agentConfigData) {
      setAgentForm((prev) => {
        if (Object.keys(prev).length === 0) {
          return { ...agentConfigData };
        }
        return prev;
      });
    }
  }, [agentConfigData]);

  const handleAgentApply = useCallback(() => {
    if (!agentConfigData) return;
    const payload: AgentConfigUpdateRequest = {};
    if (
      agentForm.agent_max_iterations !== undefined &&
      agentForm.agent_max_iterations !== agentConfigData.agent_max_iterations
    )
      payload.agent_max_iterations = agentForm.agent_max_iterations;
    if (
      agentForm.agent_timeout_seconds !== undefined &&
      agentForm.agent_timeout_seconds !== agentConfigData.agent_timeout_seconds
    )
      payload.agent_timeout_seconds = agentForm.agent_timeout_seconds;
    if (
      agentForm.skills_char_budget !== undefined &&
      agentForm.skills_char_budget !== agentConfigData.skills_char_budget
    )
      payload.skills_char_budget = agentForm.skills_char_budget;

    if (Object.keys(payload).length === 0) {
      notification.info({ message: "无变更" });
      return;
    }
    updateAgentMutation.mutate(payload, {
      onSuccess: (data) => {
        notification.success({ message: "执行参数已更新" });
        setAgentForm({ ...data });
      },
    });
  }, [agentForm, agentConfigData, updateAgentMutation]);

  const [forms, setForms] = useState<Record<string, ConfigFormState>>({});

  const selectedKey = location.pathname === "/" ? "/" : location.pathname;

  // Sync form state from server data
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
      if (
        form.max_retries !== undefined &&
        form.max_retries !== config.max_retries
      )
        payload.max_retries = form.max_retries;
      if (
        form.temperature !== undefined &&
        form.temperature !== config.temperature
      )
        payload.temperature = form.temperature;
      if (form.top_p !== undefined && form.top_p !== config.top_p)
        payload.top_p = form.top_p;
      if (form.max_tokens !== undefined && form.max_tokens !== config.max_tokens)
        payload.max_tokens = form.max_tokens;
      if (form.enabled !== undefined && form.enabled !== config.enabled)
        payload.enabled = form.enabled;
      if (form.api_key) payload.api_key = form.api_key;

      if (Object.keys(payload).length === 0) {
        notification.info({ message: "无变更" });
        return;
      }
      updateMutation.mutate(
        { name, req: payload },
        {
          onSuccess: () => {
            notification.success({ message: "配置已更新" });
            setForms((prev) => ({
              ...prev,
              [name]: { ...prev[name], api_key: undefined },
            }));
          },
        },
      );
    },
    [forms, configsData, updateMutation],
  );

  const handleAdd = useCallback(() => {
    addForm.validateFields().then((values) => {
      createMutation.mutate(values, {
        onSuccess: () => {
          notification.success({ message: `配置 "${values.name}" 已添加` });
          setAddModalOpen(false);
          addForm.resetFields();
        },
        onError: (err: unknown) => {
          const msg =
            err instanceof Error ? err.message : "名称已存在";
          notification.error({ message: msg });
        },
      });
    });
  }, [addForm, createMutation]);

  const configs = configsData?.configs ?? [];
  const activeName = configsData?.active_name ?? "";

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
              活跃
            </Tag>
          )}
        </span>
        <span
          style={{
            fontSize: 12,
            color: token.colorTextTertiary,
          }}
        >
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
              notification.success({
                message: `已切换活跃配置为 "${c.name}"`,
              }),
          })
        }
        onDelete={() =>
          deleteMutation.mutate(c.name, {
            onSuccess: () =>
              notification.success({ message: `配置 "${c.name}" 已删除` }),
          })
        }
        applyLoading={updateMutation.isPending}
        activateLoading={activateMutation.isPending}
      />
    ),
  }));

  return (
    <Layout.Sider
      collapsible
      collapsed={collapsed}
      onCollapse={setCollapsed}
      trigger={null}
      width={200}
      collapsedWidth={60}
      style={{ borderRight: `1px solid ${token.colorBorder}` }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
        }}
      >
        <Menu
          mode="inline"
          inlineCollapsed={collapsed}
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ flex: 1, paddingTop: 12, borderRight: "none" }}
        />

        <div
          style={{
            borderTop: `1px solid ${token.colorBorder}`,
            padding: collapsed ? "8px 0" : "8px 12px",
            display: "flex",
            flexDirection: "column",
            alignItems: collapsed ? "center" : "stretch",
            gap: 4,
          }}
        >
          <Tooltip title={collapsed ? "设置" : ""} placement="right">
            <Button
              type="text"
              icon={<SettingOutlined />}
              onClick={() => setDrawerOpen(true)}
              style={{
                color: token.colorText,
                width: collapsed ? 40 : "100%",
                justifyContent: collapsed ? "center" : "flex-start",
              }}
            >
              {collapsed ? null : "设置"}
            </Button>
          </Tooltip>
          <Tooltip
            title={collapsed ? (collapsed ? "展开" : "收起") : ""}
            placement="right"
          >
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((v) => !v)}
              style={{
                color: token.colorTextSecondary,
                width: collapsed ? 40 : "100%",
                justifyContent: collapsed ? "center" : "flex-start",
              }}
            >
              {collapsed ? null : "收起侧栏"}
            </Button>
          </Tooltip>
        </div>
      </div>

      <Drawer
        title="设置"
        placement="right"
        width={420}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        styles={{ body: { padding: 20 } }}
      >
        {/* Theme */}
        <div style={{ marginBottom: 20 }}>
          <div
            style={{
              marginBottom: 8,
              fontSize: 13,
              fontWeight: 600,
              color: token.colorText,
            }}
          >
            外观
          </div>
          <Segmented
            value={mode}
            onChange={() => toggleTheme()}
            options={[
              { value: "light", icon: <SunOutlined />, label: "浅色" },
              { value: "dark", icon: <MoonOutlined />, label: "深色" },
            ]}
            block
          />
        </div>

        <Divider style={{ margin: "16px 0" }} />

        {/* Agent Config */}
        <div style={{ marginBottom: 20 }}>
          <div
            style={{
              marginBottom: 12,
              fontSize: 13,
              fontWeight: 600,
              color: token.colorText,
            }}
          >
            Agent 参数
          </div>
          <div style={{ marginBottom: 12 }}>
            <div
              style={{
                marginBottom: 4,
                fontSize: 13,
                color: token.colorTextTertiary,
              }}
            >
              最大迭代次数
            </div>
            <InputNumber
              size="small"
              min={1}
              max={200}
              step={1}
              value={agentForm.agent_max_iterations}
              onChange={(v) =>
                setAgentForm((prev) => ({
                  ...prev,
                  agent_max_iterations: v ?? 20,
                }))
              }
              style={{ width: 140 }}
            />
          </div>
          <div style={{ marginBottom: 12 }}>
            <div
              style={{
                marginBottom: 4,
                fontSize: 13,
                color: token.colorTextTertiary,
              }}
            >
              执行超时 (秒)
            </div>
            <InputNumber
              size="small"
              min={10}
              max={3600}
              step={10}
              value={agentForm.agent_timeout_seconds}
              onChange={(v) =>
                setAgentForm((prev) => ({
                  ...prev,
                  agent_timeout_seconds: v ?? 300,
                }))
              }
              style={{ width: 140 }}
            />
          </div>

          <Divider style={{ margin: "16px 0" }} />

          <div
            style={{
              marginBottom: 12,
              fontSize: 13,
              fontWeight: 600,
              color: token.colorText,
            }}
          >
            Skill 参数
          </div>
          <div style={{ marginBottom: 12 }}>
            <div
              style={{
                marginBottom: 4,
                fontSize: 13,
                color: token.colorTextTertiary,
              }}
            >
              字符预算
            </div>
            <InputNumber
              size="small"
              min={1000}
              max={500000}
              step={1000}
              value={agentForm.skills_char_budget}
              onChange={(v) =>
                setAgentForm((prev) => ({
                  ...prev,
                  skills_char_budget: v ?? 30000,
                }))
              }
              style={{ width: 140 }}
            />
          </div>
          <Button
            size="small"
            type="primary"
            loading={updateAgentMutation.isPending}
            onClick={handleAgentApply}
          >
            应用
          </Button>
        </div>

        <Divider style={{ margin: "16px 0" }} />

        {/* LLM Configs Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 12,
          }}
        >
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: token.colorText,
            }}
          >
            LLM 配置
          </div>
          <Button
            type="text"
            size="small"
            icon={<PlusOutlined />}
            onClick={() => setAddModalOpen(true)}
          >
            添加
          </Button>
        </div>

        {isLoading ? (
          <Spin />
        ) : (
          <Collapse
            accordion
            items={collapseItems}
            style={{ background: "transparent" }}
          />
        )}
      </Drawer>

      {/* Add Config Modal */}
      <Modal
        title="添加模型配置"
        open={addModalOpen}
        onOk={handleAdd}
        onCancel={() => {
          setAddModalOpen(false);
          addForm.resetFields();
        }}
        confirmLoading={createMutation.isPending}
        okText="添加"
        cancelText="取消"
      >
        <Form form={addForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="name"
            label="配置名称"
            rules={[{ required: true, message: "请输入配置名称" }]}
          >
            <Input placeholder="例如 gpt-4o" />
          </Form.Item>
          <Form.Item
            name="model"
            label="模型"
            rules={[{ required: true, message: "请输入模型名" }]}
          >
            <Input placeholder="例如 gpt-4o" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key">
            <Input.Password placeholder="可选" />
          </Form.Item>
          <Form.Item name="api_base" label="API Base">
            <Input placeholder="可选" />
          </Form.Item>
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
    </Layout.Sider>
  );
}

// --- ConfigPanelBody (each accordion panel's content) ---

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
  const labelStyle = {
    marginBottom: 4,
    fontSize: 13,
    color: token.colorTextTertiary,
  };

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <div style={labelStyle}>Model</div>
        <Input
          size="small"
          value={form.model ?? ""}
          onChange={(e) => onFieldChange("model", e.target.value)}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={labelStyle}>API Key ({config.api_key_masked})</div>
        <Input.Password
          size="small"
          placeholder="输入新 Key 以更新"
          value={form.api_key ?? ""}
          onChange={(e) => onFieldChange("api_key", e.target.value)}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={labelStyle}>API Base</div>
        <Input
          size="small"
          placeholder="https://open.bigmodel.cn/api/paas/v4"
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
            应用
          </Button>
          {!isActive && (
            <Button size="small" loading={activateLoading} onClick={onActivate}>
              激活
            </Button>
          )}
        </div>
        {canDelete && (
          <Popconfirm
            title="确认删除此配置？"
            onConfirm={onDelete}
            okText="删除"
            cancelText="取消"
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        )}
      </div>
    </div>
  );
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
