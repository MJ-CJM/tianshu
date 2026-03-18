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
  notification,
} from "antd";
import {
  UnorderedListOutlined,
  PlusCircleOutlined,
  SettingOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from "@ant-design/icons";
import { useNavigate, useLocation } from "react-router-dom";
import { useState, useEffect } from "react";
import { useConfig, useUpdateConfig } from "../../hooks/useConfig";
import type { LLMConfigUpdateRequest } from "../../api/types";

const menuItems = [
  {
    key: "/",
    icon: <UnorderedListOutlined />,
    label: "敕令卷宗",
  },
  {
    key: "/edicts/create",
    icon: <PlusCircleOutlined />,
    label: "颁发敕令",
  },
];

export default function AppSidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const { data: config, isLoading } = useConfig();
  const mutation = useUpdateConfig();
  const [form, setForm] = useState<LLMConfigUpdateRequest>({});

  const selectedKey = location.pathname === "/" ? "/" : location.pathname;

  useEffect(() => {
    if (config) {
      setForm({
        model: config.model,
        api_base: config.api_base,
        max_retries: config.max_retries,
        temperature: config.temperature,
        top_p: config.top_p,
        max_tokens: config.max_tokens,
        enabled: config.enabled,
      });
    }
  }, [config]);

  const handleApply = () => {
    const payload: LLMConfigUpdateRequest = {};
    if (config) {
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
    }
    if (Object.keys(payload).length === 0) {
      notification.info({ message: "无变更" });
      return;
    }
    mutation.mutate(payload, {
      onSuccess: () => {
        notification.success({ message: "配置已更新" });
        setForm((prev) => ({ ...prev, api_key: undefined }));
      },
    });
  };

  return (
    <Layout.Sider
      collapsible
      collapsed={collapsed}
      onCollapse={setCollapsed}
      trigger={null}
      width={200}
      collapsedWidth={60}
      style={{ borderRight: "1px solid #1e3a5f" }}
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
            borderTop: "1px solid #1e3a5f",
            padding: collapsed ? "8px 0" : "8px 12px",
            display: "flex",
            flexDirection: "column",
            alignItems: collapsed ? "center" : "stretch",
            gap: 4,
          }}
        >
          <Tooltip title={collapsed ? "LLM 配置" : ""} placement="right">
            <Button
              type="text"
              icon={<SettingOutlined />}
              onClick={() => setDrawerOpen(true)}
              style={{
                color: "#00d4ff",
                width: collapsed ? 40 : "100%",
                justifyContent: collapsed ? "center" : "flex-start",
              }}
            >
              {collapsed ? null : "LLM 配置"}
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
                color: "rgba(255,255,255,0.45)",
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
        title="LLM 配置"
        placement="right"
        width={360}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        styles={{ body: { padding: 20 } }}
      >
        {isLoading ? (
          <Spin />
        ) : (
          <>
            <div style={{ marginBottom: 16 }}>
              <div
                style={{
                  marginBottom: 4,
                  fontSize: 13,
                  color: "rgba(255,255,255,0.65)",
                }}
              >
                Model
              </div>
              <Input
                value={form.model ?? ""}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, model: e.target.value }))
                }
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <div
                style={{
                  marginBottom: 4,
                  fontSize: 13,
                  color: "rgba(255,255,255,0.65)",
                }}
              >
                API Key ({config?.api_key_masked})
              </div>
              <Input.Password
                placeholder="输入新 Key 以更新"
                value={form.api_key ?? ""}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, api_key: e.target.value }))
                }
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <div
                style={{
                  marginBottom: 4,
                  fontSize: 13,
                  color: "rgba(255,255,255,0.65)",
                }}
              >
                API Base
              </div>
              <Input
                placeholder="https://open.bigmodel.cn/api/paas/v4"
                value={form.api_base ?? ""}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, api_base: e.target.value }))
                }
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <div
                style={{
                  marginBottom: 4,
                  fontSize: 13,
                  color: "rgba(255,255,255,0.65)",
                }}
              >
                Max Retries
              </div>
              <InputNumber
                min={0}
                max={10}
                value={form.max_retries}
                onChange={(v) =>
                  setForm((prev) => ({ ...prev, max_retries: v ?? 0 }))
                }
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <div
                style={{
                  marginBottom: 4,
                  fontSize: 13,
                  color: "rgba(255,255,255,0.65)",
                }}
              >
                Temperature
              </div>
              <InputNumber
                min={0}
                max={2}
                step={0.1}
                value={form.temperature}
                onChange={(v) =>
                  setForm((prev) => ({ ...prev, temperature: v ?? 0.7 }))
                }
                style={{ width: 120 }}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <div
                style={{
                  marginBottom: 4,
                  fontSize: 13,
                  color: "rgba(255,255,255,0.65)",
                }}
              >
                Top P
              </div>
              <InputNumber
                min={0}
                max={1}
                step={0.1}
                value={form.top_p}
                onChange={(v) =>
                  setForm((prev) => ({ ...prev, top_p: v ?? 1.0 }))
                }
                style={{ width: 120 }}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <div
                style={{
                  marginBottom: 4,
                  fontSize: 13,
                  color: "rgba(255,255,255,0.65)",
                }}
              >
                Max Tokens
              </div>
              <InputNumber
                min={1}
                max={128000}
                value={form.max_tokens}
                onChange={(v) =>
                  setForm((prev) => ({ ...prev, max_tokens: v ?? 4096 }))
                }
                style={{ width: 160 }}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <div
                style={{
                  marginBottom: 4,
                  fontSize: 13,
                  color: "rgba(255,255,255,0.65)",
                }}
              >
                Enabled
              </div>
              <Switch
                checked={form.enabled}
                onChange={(v) =>
                  setForm((prev) => ({ ...prev, enabled: v }))
                }
              />
            </div>

            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: 8,
                marginTop: 24,
              }}
            >
              <Button onClick={() => setDrawerOpen(false)}>关闭</Button>
              <Button
                type="primary"
                loading={mutation.isPending}
                onClick={handleApply}
              >
                应用
              </Button>
            </div>
          </>
        )}
      </Drawer>
    </Layout.Sider>
  );
}
