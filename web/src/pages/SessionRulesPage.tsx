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
import { useT } from "../i18n";
import PageQueryError from "../components/states/PageQueryError";

const { Text } = Typography;

const TIER_COLORS: Record<number, string> = {
  0: "green",
  1: "blue",
  2: "orange",
  3: "red",
};

const SCOPE_COLORS: Record<string, string> = {
  always: "purple",
  edict: "cyan",
};

const SOURCE_COLORS: Record<string, string> = {
  approval: "blue",
  profile: "geekblue",
  manual: "default",
};

export default function SessionRulesPage() {
  const t = useT();
  const [rules, setRules] = useState<SessionRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [scope, setScope] = useState<"edict" | "always" | "all">("all");

  // Add modal
  const [addOpen, setAddOpen] = useState(false);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [toolsLoading, setToolsLoading] = useState(false);
  const [toolsError, setToolsError] = useState<unknown>(null);
  const [addSubmitting, setAddSubmitting] = useState(false);
  const [form] = Form.useForm();

  const load = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await fetchSessionRules(scope);
      setRules(data);
    } catch (error) {
      setLoadError(error);
    } finally {
      setLoading(false);
    }
  };

  const loadTools = async () => {
    if (tools.length > 0) return;
    setToolsLoading(true);
    setToolsError(null);
    try {
      const data = await fetchTools();
      setTools(data);
    } catch (error) {
      setToolsError(error);
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
      message.success(t("toast.ruleRevoked"));
      await load();
    } catch {
      message.error(t("toast.ruleRevokeFailed"));
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
      message.success(t("toast.ruleCreated"));
      form.resetFields();
      setAddOpen(false);
      await load();
    } catch (err: unknown) {
      if (err && typeof err === "object" && "errorFields" in err) return;
      const msg = err instanceof Error ? err.message : t("toast.ruleCreateFailed");
      message.error(msg);
    } finally {
      setAddSubmitting(false);
    }
  };

  const columns: ColumnsType<SessionRule> = [
    {
      title: t("table.sessionRules.column.tool"),
      dataIndex: "tool_name",
      width: 160,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: t("table.sessionRules.column.fingerprint"),
      dataIndex: "arg_fingerprint",
      ellipsis: true,
      render: (v: string) =>
        v === "*" ? (
          <Tag>{t("table.sessionRules.cell.anyArg")}</Tag>
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
      title: t("table.sessionRules.column.scope"),
      dataIndex: "scope",
      width: 100,
      render: (v: string) => (
        <Tag color={SCOPE_COLORS[v] ?? "default"}>{t(`sessionRules.scope.${v}`)}</Tag>
      ),
    },
    {
      title: t("table.sessionRules.column.source"),
      dataIndex: "source",
      width: 110,
      render: (v: string) => (
        <Tag color={SOURCE_COLORS[v] ?? "default"}>{t(`sessionRules.source.${v}`)}</Tag>
      ),
    },
    {
      title: t("table.sessionRules.column.grantedAt"),
      dataIndex: "granted_at",
      width: 180,
      render: (v: string) => new Date(v).toLocaleString(),
      sorter: (a, b) =>
        new Date(a.granted_at).getTime() - new Date(b.granted_at).getTime(),
      defaultSortOrder: "descend",
    },
    {
      title: t("table.sessionRules.column.expiresAt"),
      dataIndex: "expires_at",
      width: 120,
      render: (v: string | null) =>
        v ? (
          new Date(v).toLocaleDateString()
        ) : (
          <Text type="secondary">{t("table.sessionRules.cell.never")}</Text>
        ),
    },
    {
      title: t("table.sessionRules.column.reason"),
      dataIndex: "reason",
      ellipsis: true,
      render: (v: string) => (
        <Tooltip title={v}>
          <span>{v}</span>
        </Tooltip>
      ),
    },
    {
      title: t("table.sessionRules.column.action"),
      width: 90,
      render: (_: unknown, row: SessionRule) => (
        <Popconfirm
          title={t("sessionRules.popconfirm.title")}
          description={t("sessionRules.popconfirm.description")}
          onConfirm={() => handleRevoke(row.rule_id)}
          okText={t("action.ok")}
          cancelText={t("common.cancel")}
        >
          <Button size="small" danger icon={<DeleteOutlined />}>
            {t("action.revoke")}
          </Button>
        </Popconfirm>
      ),
    },
  ];

  // Tool select options grouped by tier
  const toolOptions = tools.map((tool) => ({
    value: tool.name,
    label: (
      <Space size={4}>
        <Text code style={{ fontSize: 12 }}>
          {tool.name}
        </Text>
        <Tag
          color={TIER_COLORS[tool.tier] ?? "default"}
          style={{ fontSize: 10, lineHeight: "16px" }}
        >
          {t(`sessionRules.tier.${tool.tier}`)}
        </Tag>
      </Space>
    ),
  }));

  return (
    <PageContainer
      title={t("page.sessionRules.title")}
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
            {t("page.sessionRules.addRule")}
          </Button>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            {t("action.refresh")}
          </Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        icon={<SafetyCertificateOutlined />}
        message={t("page.sessionRules.alertTitle")}
        description={t("page.sessionRules.alertDesc")}
        style={{ marginBottom: 16 }}
      />

      {loadError ? (
        <PageQueryError error={loadError} onRetry={() => void load()} />
      ) : (
      <Card size="small">
        <Space style={{ marginBottom: 16 }} wrap>
          <Select
            value={scope}
            onChange={(v: "edict" | "always" | "all") => setScope(v)}
            style={{ width: 160 }}
            options={[
              { value: "all", label: t("sessionRules.scopeFilter.all") },
              { value: "always", label: t("sessionRules.scopeFilter.always") },
              { value: "edict", label: t("sessionRules.scopeFilter.edict") },
            ]}
          />
          <Select
            value={sourceFilter}
            onChange={setSourceFilter}
            style={{ width: 160 }}
            options={[
              { value: "all", label: t("sessionRules.sourceFilter.all") },
              { value: "approval", label: t("sessionRules.sourceFilter.approval") },
              { value: "profile", label: t("sessionRules.sourceFilter.profile") },
              { value: "manual", label: t("sessionRules.sourceFilter.manual") },
            ]}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t("page.sessionRules.summary", { n: filtered.length })}
          </Text>
        </Space>
        <Table<SessionRule>
          columns={columns}
          dataSource={filtered}
          rowKey="rule_id"
          loading={loading}
          pagination={{ pageSize: 20 }}
          locale={{ emptyText: t("page.sessionRules.empty") }}
          size="small"
        />
      </Card>
      )}

      <Modal
        title={t("page.sessionRules.addModalTitle")}
        open={addOpen}
        onOk={handleAdd}
        onCancel={() => {
          form.resetFields();
          setAddOpen(false);
        }}
        confirmLoading={addSubmitting}
        okText={t("action.create")}
        cancelText={t("common.cancel")}
      >
        {toolsError ? (
          <PageQueryError error={toolsError} onRetry={() => void loadTools()} />
        ) : null}
        <Form
          form={form}
          layout="vertical"
          initialValues={{ scope: "always", expires_days: 30 }}
        >
          <Form.Item
            name="tool_name"
            label={t("form.sessionRule.field.toolName")}
            rules={[{ required: true, message: t("form.sessionRule.validation.toolNameRequired") }]}
          >
            <Select
              showSearch
              loading={toolsLoading}
              placeholder={t("form.sessionRule.placeholder.toolName")}
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
            label={t("form.sessionRule.field.scope")}
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "always", label: t("form.sessionRule.scopeOption.always") },
                { value: "edict", label: t("form.sessionRule.scopeOption.edict") },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="expires_days"
            label={t("form.sessionRule.field.expiresDays")}
            tooltip={t("form.sessionRule.tooltip.expiresDays")}
          >
            <InputNumber
              min={0}
              max={365}
              placeholder={t("form.sessionRule.placeholder.expiresDays")}
              style={{ width: "100%" }}
              addonAfter={t("unit.days")}
            />
          </Form.Item>
          <Form.Item name="reason" label={t("form.sessionRule.field.reason")}>
            <Input.TextArea rows={2} placeholder={t("form.sessionRule.placeholder.reason")} />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
