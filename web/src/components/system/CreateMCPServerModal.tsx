import { useState } from "react";
import {
  Modal,
  Form,
  Input,
  InputNumber,
  Select,
  Switch,
  Space,
  Button,
  notification,
  Typography,
  Tabs,
} from "antd";
import { PlusOutlined, MinusCircleOutlined } from "@ant-design/icons";
import { useT } from "../../i18n";
import { useCreateMCPServer } from "../../hooks/useMCP";
import type { MCPServerCreate } from "../../api/mcp";

interface FormValues {
  name: string;
  transport: "stdio" | "streamable_http";
  enabled: boolean;
  default_tier: number;
  timeout: number;
  connect_timeout: number;
  command?: string;
  args_text?: string;
  env_pairs?: { key: string; value: string }[];
  url?: string;
  header_pairs?: { key: string; value: string }[];
  tools_include_text?: string;
  tools_exclude_text?: string;
}

function pairsToRecord(
  pairs: { key: string; value: string }[] | undefined,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of pairs ?? []) {
    if (p && p.key) out[p.key] = p.value ?? "";
  }
  return out;
}

function textToList(text: string | undefined): string[] {
  return (text ?? "")
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function CreateMCPServerModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const t = useT();
  const [form] = Form.useForm<FormValues>();
  const [transport, setTransport] = useState<"stdio" | "streamable_http">(
    "stdio",
  );
  const create = useCreateMCPServer();

  const handleOk = async () => {
    try {
      const v = await form.validateFields();
      const body: MCPServerCreate = {
        name: v.name.trim(),
        transport: v.transport,
        enabled: v.enabled,
        default_tier: v.default_tier,
        timeout: v.timeout,
        connect_timeout: v.connect_timeout,
        tools_include: textToList(v.tools_include_text),
        tools_exclude: textToList(v.tools_exclude_text),
      };
      if (v.transport === "stdio") {
        body.command = v.command;
        body.args = textToList(v.args_text);
        body.env = pairsToRecord(v.env_pairs);
      } else {
        body.url = v.url;
        body.headers = pairsToRecord(v.header_pairs);
      }
      const res = await create.mutateAsync(body);
      notification.success({
        message: t("system.mcp.notify.createdTitle"),
        description: t("system.mcp.notify.createdDesc", {
          name: res.data?.name ?? body.name,
          status: res.data?.status ?? "unknown",
        }),
      });
      form.resetFields();
      onClose();
    } catch (e: unknown) {
      // antd validate 抛 errorFields 时静默；其它错走提示
      if (e && typeof e === "object" && "errorFields" in e) return;
      notification.error({
        message: t("system.mcp.notify.errorTitle"),
        description: String(e),
      });
    }
  };

  return (
    <Modal
      title={t("system.mcp.create.title")}
      open={open}
      onOk={handleOk}
      onCancel={() => {
        form.resetFields();
        onClose();
      }}
      okButtonProps={{ loading: create.isPending }}
      okText={t("system.mcp.create.submit")}
      width={700}
      destroyOnClose
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          transport: "stdio",
          enabled: true,
          default_tier: 2,
          timeout: 120,
          connect_timeout: 30,
        }}
        onValuesChange={(changed) => {
          if (changed.transport) setTransport(changed.transport);
        }}
      >
        <Form.Item
          name="name"
          label={t("system.mcp.create.name")}
          rules={[
            { required: true, message: t("system.mcp.create.nameRequired") },
            {
              pattern: /^[A-Za-z0-9.-]+$/,
              message: t("system.mcp.create.namePattern"),
            },
          ]}
          extra={t("system.mcp.create.nameHint")}
        >
          <Input placeholder="github / filesystem / playwright" />
        </Form.Item>

        <Form.Item
          name="transport"
          label={t("system.mcp.create.transport")}
          rules={[{ required: true }]}
        >
          <Select
            options={[
              { value: "stdio", label: "stdio (local subprocess)" },
              { value: "streamable_http", label: "streamable HTTP (remote)" },
            ]}
          />
        </Form.Item>

        {transport === "stdio" ? (
          <>
            <Form.Item
              name="command"
              label={t("system.mcp.create.command")}
              rules={[
                {
                  required: true,
                  message: t("system.mcp.create.commandRequired"),
                },
              ]}
            >
              <Input placeholder="npx" />
            </Form.Item>
            <Form.Item
              name="args_text"
              label={t("system.mcp.create.args")}
              extra={t("system.mcp.create.argsHint")}
            >
              <Input.TextArea
                rows={3}
                placeholder={
                  "-y\n@modelcontextprotocol/server-filesystem\n/tmp/sandbox"
                }
              />
            </Form.Item>
            <Form.Item label={t("system.mcp.create.env")}>
              <Form.List name="env_pairs">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map((field) => (
                      <Space
                        key={field.key}
                        align="baseline"
                        style={{ display: "flex", marginBottom: 4 }}
                      >
                        <Form.Item
                          name={[field.name, "key"]}
                          rules={[{ required: true, message: "key" }]}
                          style={{ marginBottom: 0 }}
                        >
                          <Input placeholder="KEY" />
                        </Form.Item>
                        <Form.Item
                          name={[field.name, "value"]}
                          style={{ marginBottom: 0 }}
                        >
                          <Input placeholder="value or ${ENV_VAR}" />
                        </Form.Item>
                        <Button
                          type="text"
                          icon={<MinusCircleOutlined />}
                          onClick={() => remove(field.name)}
                        />
                      </Space>
                    ))}
                    <Button
                      type="dashed"
                      icon={<PlusOutlined />}
                      onClick={() => add({ key: "", value: "" })}
                    >
                      {t("system.mcp.create.addRow")}
                    </Button>
                  </>
                )}
              </Form.List>
            </Form.Item>
          </>
        ) : (
          <>
            <Form.Item
              name="url"
              label={t("system.mcp.create.url")}
              rules={[
                {
                  required: true,
                  message: t("system.mcp.create.urlRequired"),
                },
                { type: "url", message: t("system.mcp.create.urlInvalid") },
              ]}
            >
              <Input placeholder="https://api.githubcopilot.com/mcp" />
            </Form.Item>
            <Form.Item label={t("system.mcp.create.headers")}>
              <Form.List name="header_pairs">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map((field) => (
                      <Space
                        key={field.key}
                        align="baseline"
                        style={{ display: "flex", marginBottom: 4 }}
                      >
                        <Form.Item
                          name={[field.name, "key"]}
                          rules={[{ required: true, message: "header" }]}
                          style={{ marginBottom: 0 }}
                        >
                          <Input placeholder="Authorization" />
                        </Form.Item>
                        <Form.Item
                          name={[field.name, "value"]}
                          style={{ marginBottom: 0 }}
                        >
                          <Input placeholder="Bearer ${MCP_TOKEN}" />
                        </Form.Item>
                        <Button
                          type="text"
                          icon={<MinusCircleOutlined />}
                          onClick={() => remove(field.name)}
                        />
                      </Space>
                    ))}
                    <Button
                      type="dashed"
                      icon={<PlusOutlined />}
                      onClick={() => add({ key: "", value: "" })}
                    >
                      {t("system.mcp.create.addRow")}
                    </Button>
                  </>
                )}
              </Form.List>
            </Form.Item>
          </>
        )}

        <Tabs
          size="small"
          items={[
            {
              key: "advanced",
              label: t("system.mcp.create.advanced"),
              children: (
                <>
                  <Form.Item
                    name="default_tier"
                    label={t("system.mcp.create.defaultTier")}
                    extra={t("system.mcp.create.defaultTierHint")}
                  >
                    <Select
                      options={[
                        { value: 0, label: "T0 — readonly" },
                        { value: 1, label: "T1 — workspace write" },
                        { value: 2, label: "T2 — network (default)" },
                        { value: 3, label: "T3 — external write" },
                        { value: 4, label: "T4 — dangerous" },
                      ]}
                    />
                  </Form.Item>
                  <Space>
                    <Form.Item
                      name="timeout"
                      label={t("system.mcp.create.timeout")}
                    >
                      <InputNumber min={1} max={3600} />
                    </Form.Item>
                    <Form.Item
                      name="connect_timeout"
                      label={t("system.mcp.create.connectTimeout")}
                    >
                      <InputNumber min={1} max={300} />
                    </Form.Item>
                    <Form.Item
                      name="enabled"
                      label={t("system.mcp.create.enabled")}
                      valuePropName="checked"
                    >
                      <Switch />
                    </Form.Item>
                  </Space>
                  <Form.Item
                    name="tools_include_text"
                    label={t("system.mcp.create.toolsInclude")}
                    extra={t("system.mcp.create.toolsFilterHint")}
                  >
                    <Input.TextArea rows={2} />
                  </Form.Item>
                  <Form.Item
                    name="tools_exclude_text"
                    label={t("system.mcp.create.toolsExclude")}
                  >
                    <Input.TextArea rows={2} />
                  </Form.Item>
                </>
              ),
            },
          ]}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
          {t("system.mcp.create.envHint")}
        </Typography.Paragraph>
      </Form>
    </Modal>
  );
}
