import { useEffect, useState } from "react";
import {
  Table,
  Button,
  Tag,
  Popconfirm,
  message,
  Card,
  Select,
  Space,
  Typography,
  Alert,
  Tooltip,
  Modal,
  Form,
  Input,
  InputNumber,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ReloadOutlined,
  SafetyCertificateOutlined,
  DeleteOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
import {
  fetchSessionRules,
  revokeSessionRule,
  createSessionRule,
  fetchTools,
} from "../api/policy";
import type { SessionRule, ToolInfo } from "../api/policy";

const { Text } = Typography;

const TIER_LABELS: Record<number, string> = {
  0: "T0 只读",
  1: "T1 工作区",
  2: "T2 写入",
  3: "T3 危险",
};
const TIER_COLORS: Record<number, string> = {
  0: "green",
  1: "blue",
  2: "orange",
  3: "red",
};

const scopeLabel: Record<string, string> = {
  always: "全局",
  edict: "敕令级",
};
const scopeColor: Record<string, string> = {
  always: "purple",
  edict: "cyan",
};
const sourceLabel: Record<string, string> = {
  approval: "审批授予",
  profile: "策略模板",
  manual: "手动添加",
};
const sourceColor: Record<string, string> = {
  approval: "blue",
  profile: "geekblue",
  manual: "default",
};

export default function SessionRulesPage() {
  const [rules, setRules] = useState<SessionRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [scope, setScope] = useState<"edict" | "always" | "all">("all");

  // Add modal
  const [addOpen, setAddOpen] = useState(false);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [toolsLoading, setToolsLoading] = useState(false);
  const [addSubmitting, setAddSubmitting] = useState(false);
  const [form] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchSessionRules(scope);
      setRules(data);
    } finally {
      setLoading(false);
    }
  };

  const loadTools = async () => {
    if (tools.length > 0) return;
    setToolsLoading(true);
    try {
      const data = await fetchTools();
      setTools(data);
    } finally {
      setToolsLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  const filtered =
    sourceFilter === "all"
      ? rules
      : rules.filter((r) => r.source === sourceFilter);

  const handleRevoke = async (ruleId: string) => {
    try {
      await revokeSessionRule(ruleId);
      message.success("规则已撤销");
      await load();
    } catch {
      message.error("撤销失败");
    }
  };

  const handleAdd = async () => {
    try {
      const values = await form.validateFields();
      setAddSubmitting(true);
      await createSessionRule({
        tool_name: values.tool_name,
        scope: values.scope,
        reason: values.reason || undefined,
        expires_days: values.expires_days || null,
      });
      message.success("规则已创建");
      form.resetFields();
      setAddOpen(false);
      await load();
    } catch (err: unknown) {
      if (err && typeof err === "object" && "errorFields" in err) return;
      const msg = err instanceof Error ? err.message : "创建失败";
      message.error(msg);
    } finally {
      setAddSubmitting(false);
    }
  };

  const columns: ColumnsType<SessionRule> = [
    {
      title: "工具",
      dataIndex: "tool_name",
      width: 160,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: "参数指纹",
      dataIndex: "arg_fingerprint",
      ellipsis: true,
      render: (v: string) =>
        v === "*" ? (
          <Tag>任意参数</Tag>
        ) : v ? (
          <Tooltip title={v}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {v.slice(0, 16)}…
            </Text>
          </Tooltip>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "范围",
      dataIndex: "scope",
      width: 100,
      render: (v: string) => (
        <Tag color={scopeColor[v] ?? "default"}>{scopeLabel[v] ?? v}</Tag>
      ),
    },
    {
      title: "来源",
      dataIndex: "source",
      width: 110,
      render: (v: string) => (
        <Tag color={sourceColor[v] ?? "default"}>{sourceLabel[v] ?? v}</Tag>
      ),
    },
    {
      title: "授予时间",
      dataIndex: "granted_at",
      width: 180,
      render: (v: string) => new Date(v).toLocaleString(),
      sorter: (a, b) =>
        new Date(a.granted_at).getTime() - new Date(b.granted_at).getTime(),
      defaultSortOrder: "descend",
    },
    {
      title: "过期时间",
      dataIndex: "expires_at",
      width: 120,
      render: (v: string | null) =>
        v ? (
          new Date(v).toLocaleDateString()
        ) : (
          <Text type="secondary">永不</Text>
        ),
    },
    {
      title: "原因",
      dataIndex: "reason",
      ellipsis: true,
      render: (v: string) => (
        <Tooltip title={v}>
          <span>{v}</span>
        </Tooltip>
      ),
    },
    {
      title: "操作",
      width: 90,
      render: (_: unknown, row: SessionRule) => (
        <Popconfirm
          title="确定撤销此规则？"
          description="撤销后相关工具调用将重新需要审批"
          onConfirm={() => handleRevoke(row.rule_id)}
          okText="确定"
          cancelText="取消"
        >
          <Button size="small" danger icon={<DeleteOutlined />}>
            撤销
          </Button>
        </Popconfirm>
      ),
    },
  ];

  // Tool select options grouped by tier
  const toolOptions = tools.map((t) => ({
    value: t.name,
    label: (
      <Space size={4}>
        <Text code style={{ fontSize: 12 }}>
          {t.name}
        </Text>
        <Tag
          color={TIER_COLORS[t.tier] ?? "default"}
          style={{ fontSize: 10, lineHeight: "16px" }}
        >
          {TIER_LABELS[t.tier] ?? `T${t.tier}`}
        </Tag>
      </Space>
    ),
  }));

  return (
    <PageContainer
      title="权印司 · Session Rules"
      extra={
        <Space>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              loadTools();
              setAddOpen(true);
            }}
          >
            添加规则
          </Button>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        icon={<SafetyCertificateOutlined />}
        message="Session Rules 是工具调用的免审令牌"
        description="当你在御书房朱批工具调用时选择「本敕令」或「全局」范围，系统会自动创建 session rule。你也可以手动添加规则来预配置免审权限。注意：shell_exec/bash 工具不允许设为全局免审。"
        style={{ marginBottom: 16 }}
      />

      <Card size="small">
        <Space style={{ marginBottom: 16 }} wrap>
          <Select
            value={scope}
            onChange={(v: "edict" | "always" | "all") => setScope(v)}
            style={{ width: 160 }}
            options={[
              { value: "all", label: "全部范围" },
              { value: "always", label: "全局 (always)" },
              { value: "edict", label: "敕令级 (edict)" },
            ]}
          />
          <Select
            value={sourceFilter}
            onChange={setSourceFilter}
            style={{ width: 160 }}
            options={[
              { value: "all", label: "全部来源" },
              { value: "approval", label: "审批授予" },
              { value: "profile", label: "策略模板" },
              { value: "manual", label: "手动添加" },
            ]}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            共 {filtered.length} 条规则
          </Text>
        </Space>
        <Table<SessionRule>
          columns={columns}
          dataSource={filtered}
          rowKey="rule_id"
          loading={loading}
          pagination={{ pageSize: 20 }}
          locale={{
            emptyText:
              "暂无规则 — 点击右上角「添加规则」手动创建，或在御书房朱批时选择「本敕令/全局」自动生成",
          }}
          size="small"
        />
      </Card>

      <Modal
        title="添加免审规则"
        open={addOpen}
        onOk={handleAdd}
        onCancel={() => {
          form.resetFields();
          setAddOpen(false);
        }}
        confirmLoading={addSubmitting}
        okText="创建"
        cancelText="取消"
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ scope: "always", expires_days: 30 }}
        >
          <Form.Item
            name="tool_name"
            label="工具名称"
            rules={[{ required: true, message: "请选择工具" }]}
          >
            <Select
              showSearch
              loading={toolsLoading}
              placeholder="搜索或选择工具…"
              options={toolOptions}
              filterOption={(input, option) =>
                typeof option?.value === "string" &&
                option.value.toLowerCase().includes(input.toLowerCase())
              }
              optionLabelProp="value"
            />
          </Form.Item>
          <Form.Item
            name="scope"
            label="范围"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "always", label: "全局 — 所有敕令生效" },
                { value: "edict", label: "敕令级 — 仅当前指定敕令" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="expires_days"
            label="有效天数"
            tooltip="留空或填 0 表示永不过期（全局规则默认 30 天）"
          >
            <InputNumber
              min={0}
              max={365}
              placeholder="30"
              style={{ width: "100%" }}
              addonAfter="天"
            />
          </Form.Item>
          <Form.Item name="reason" label="原因 / 备注">
            <Input.TextArea rows={2} placeholder="可选，记录为什么添加此规则" />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
