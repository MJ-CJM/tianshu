import { useState, useCallback, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Form,
  Space,
  Segmented,
  Button,
  Alert,
  Table,
  Tag,
  Empty,
  Typography,
  Modal,
  Input,
  Select,
  Switch,
  Popconfirm,
  notification,
} from "antd";
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  LockOutlined,
} from "@ant-design/icons";
import type { Credential, CredentialCreate } from "../../api/types";
import {
  listCredentials,
  createCredential,
  deleteCredential,
  updateCredential,
} from "../../api/credentials";
import { isApiProblem } from "../../api/client";
import { useT } from "../../i18n";
import { monoStyle } from "./shared";

function problemMessage(error: unknown): string {
  if (isApiProblem(error)) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

export default function ExternalCredentialsTab() {
  const t = useT();
  const qc = useQueryClient();
  const [kind, setKind] = useState<"edict_auth" | "engine_provider">(
    "edict_auth",
  );
  const [items, setItems] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [vaultUnavailable, setVaultUnavailable] = useState(false);
  const [form] = Form.useForm();
  const [editOpen, setEditOpen] = useState(false);
  const [editRow, setEditRow] = useState<Credential | null>(null);
  const [editForm] = Form.useForm();

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listCredentials(kind);
      setItems(data);
      setVaultUnavailable(false);
    } catch (e: unknown) {
      const status = isApiProblem(e) ? e.status : null;
      const detail = problemMessage(e);
      // 503 + vault unavailable → 引导页（非错误提示）
      if (
        status === 503 &&
        ((isApiProblem(e) && e.code === "vault-unavailable") ||
          /vault.*unavailable|MASTER_KEY/i.test(detail))
      ) {
        setVaultUnavailable(true);
      } else {
        notification.error({
          message: t("system.toast.credLoadFailed"),
          description: detail,
        });
      }
    } finally {
      setLoading(false);
    }
  }, [kind, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  const onCreate = async (values: any) => {
    try {
      const payload: CredentialCreate = {
        name: values.name,
        value: values.value,
        kind,
      };
      if (kind === "edict_auth") {
        payload.host_pattern = values.host_pattern;
        payload.header_template =
          values.header_template || "Authorization: Bearer {value}";
      } else {
        payload.provider_name = values.provider_name;
      }
      await createCredential(payload);
      notification.success({
        message:
          kind === "edict_auth" ? t("system.toast.credCreated") : t("system.toast.credCreatedLive"),
      });
      setModalOpen(false);
      form.resetFields();
      reload();
      if (kind === "engine_provider") {
        qc.invalidateQueries({ queryKey: ["hongluisi", "engine-status"] });
      }
    } catch (e: unknown) {
      notification.error({
        message: t("system.toast.credCreateFailed"),
        description: problemMessage(e),
      });
    }
  };

  const onDelete = async (id: string) => {
    try {
      await deleteCredential(id);
      notification.success({ message: t("system.toast.credDeleted") });
      reload();
      if (kind === "engine_provider") {
        qc.invalidateQueries({ queryKey: ["hongluisi", "engine-status"] });
      }
    } catch (e: unknown) {
      notification.error({
        message: t("system.toast.credDeleteFailed"),
        description: problemMessage(e),
      });
    }
  };

  const openEdit = (row: Credential) => {
    setEditRow(row);
    editForm.setFieldsValue({
      value: "",
      extra_headers: JSON.stringify(row.extra_headers ?? {}, null, 2),
    });
    setEditOpen(true);
  };

  const onEditSubmit = async (values: any) => {
    if (!editRow) return;
    const patch: { value?: string; extra_headers?: Record<string, string>; enabled?: boolean } = {};
    if (values.value) patch.value = values.value;
    if (editRow.kind === "edict_auth" && values.extra_headers !== undefined) {
      try {
        patch.extra_headers = JSON.parse(values.extra_headers || "{}");
      } catch {
        notification.error({ message: t("system.toast.extraHeadersInvalid") });
        return;
      }
    }
    try {
      await updateCredential(editRow.id, patch);
      notification.success({ message: t("system.toast.credSaved") });
      setEditOpen(false);
      editForm.resetFields();
      reload();
      if (editRow.kind === "engine_provider") {
        qc.invalidateQueries({ queryKey: ["hongluisi", "engine-status"] });
      }
    } catch (e: unknown) {
      notification.error({
        message: t("system.toast.credSaveFailed"),
        description: problemMessage(e),
      });
    }
  };

  const toggleEnabled = async (row: Credential, next: boolean) => {
    try {
      await updateCredential(row.id, { enabled: next });
      notification.success({ message: next ? t("system.toast.toolEnabled", { name: row.name }) : t("system.toast.toolDisabled", { name: row.name }) });
      reload();
      if (row.kind === "engine_provider") {
        qc.invalidateQueries({ queryKey: ["hongluisi", "engine-status"] });
      }
    } catch (e: unknown) {
      notification.error({
        message: t("system.toast.credToggleFailed"),
        description: problemMessage(e),
      });
    }
  };

  const edictColumns = [
    { title: t("system.externalCreds.table.name"), dataIndex: "name", key: "name" },
    {
      title: t("system.externalCreds.table.enabled"),
      dataIndex: "enabled",
      key: "enabled",
      width: 70,
      render: (v: boolean, row: Credential) => (
        <Switch
          size="small"
          checked={v}
          onChange={(next) => toggleEnabled(row, next)}
        />
      ),
    },
    {
      title: t("system.externalCreds.table.hostPattern"),
      dataIndex: "host_pattern",
      key: "host_pattern",
      render: (v: string) => <code style={monoStyle}>{v}</code>,
    },
    {
      title: t("system.externalCreds.table.headerTemplate"),
      dataIndex: "header_template",
      key: "header_template",
      render: (v: string) => (
        <code style={monoStyle}>{v.replace(/\{value\}/, "•••")}</code>
      ),
    },
    {
      title: t("system.externalCreds.table.lastUsed"),
      dataIndex: "last_used_at",
      key: "last_used_at",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("system.externalCreds.table.actions"),
      key: "actions",
      width: 160,
      render: (_: unknown, row: Credential) => (
        <Space size={4}>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(row)}
          >
            {t("action.edit")}
          </Button>
          <Popconfirm
            title={t("system.externalCreds.delEdictPopconfirm")}
            onConfirm={() => onDelete(row.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              {t("action.delete")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const providerColumns = [
    { title: t("system.externalCreds.table.name"), dataIndex: "name", key: "name" },
    {
      title: t("system.externalCreds.table.enabled"),
      dataIndex: "enabled",
      key: "enabled",
      width: 70,
      render: (v: boolean, row: Credential) => (
        <Switch
          size="small"
          checked={v}
          onChange={(next) => toggleEnabled(row, next)}
        />
      ),
    },
    {
      title: t("system.externalCreds.table.provider"),
      dataIndex: "provider_name",
      key: "provider_name",
      render: (v: string | null) =>
        v ? <Tag color="geekblue">{v}</Tag> : "—",
    },
    {
      title: t("system.externalCreds.table.lastUsed"),
      dataIndex: "last_used_at",
      key: "last_used_at",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("system.externalCreds.table.actions"),
      key: "actions",
      width: 160,
      render: (_: unknown, row: Credential) => (
        <Space size={4}>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(row)}
          >
            {t("action.edit")}
          </Button>
          <Popconfirm
            title={t("system.externalCreds.delEnginePopconfirm")}
            onConfirm={() => onDelete(row.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              {t("action.delete")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  if (vaultUnavailable) {
    return (
      <Alert
        type="warning"
        showIcon
        icon={<LockOutlined />}
        message={t("system.externalCreds.vaultTitle")}
        description={
          <Space direction="vertical" style={{ width: "100%", marginTop: 4 }}>
            <Typography.Paragraph style={{ marginBottom: 4 }}>
              {t("system.externalCreds.vaultDescPara1")}
            </Typography.Paragraph>
            <Typography.Text strong>{t("system.externalCreds.vaultStepLabel")}</Typography.Text>
            <Typography.Paragraph style={{ marginBottom: 4 }}>
              {t("system.externalCreds.vaultStep1")}
            </Typography.Paragraph>
            <pre
              style={{
                ...monoStyle,
                background: "rgba(0,0,0,0.2)",
                padding: "8px 12px",
                borderRadius: 4,
                margin: 0,
                whiteSpace: "pre-wrap",
              }}
            >
{`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`}
            </pre>
            <Typography.Paragraph style={{ marginBottom: 4, marginTop: 8 }}>
              {t("system.externalCreds.vaultStep2")}
            </Typography.Paragraph>
            <pre
              style={{
                ...monoStyle,
                background: "rgba(0,0,0,0.2)",
                padding: "8px 12px",
                borderRadius: 4,
                margin: 0,
              }}
            >
{`TIANSHU_SECRET_MASTER_KEY=<key>`}
            </pre>
            <Typography.Paragraph style={{ marginBottom: 0, marginTop: 8 }}>
              {t("system.externalCreds.vaultStep3")}
            </Typography.Paragraph>
            <Space style={{ marginTop: 12 }}>
              <Button type="primary" onClick={reload}>
                {t("system.externalCreds.vaultRecheck")}
              </Button>
            </Space>
          </Space>
        }
      />
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space style={{ justifyContent: "space-between", width: "100%" }}>
        <Segmented
          value={kind}
          onChange={(v) =>
            setKind(v as "edict_auth" | "engine_provider")
          }
          options={[
            { value: "edict_auth", label: t("system.externalCreds.edictAuthLabel") },
            { value: "engine_provider", label: t("system.externalCreds.engineLabel") },
          ]}
        />
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setModalOpen(true)}
        >
          {t("system.externalCreds.add")}
        </Button>
      </Space>

      {kind === "engine_provider" && (
        <Alert
          type="info"
          message={t("system.externalCreds.engineAlertMsg")}
          description={t("system.externalCreds.engineAlertDesc")}
          showIcon
          style={{ marginBottom: 8 }}
        />
      )}

      <Table
        rowKey="id"
        columns={kind === "edict_auth" ? edictColumns : providerColumns}
        dataSource={items}
        loading={loading}
        pagination={false}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Typography.Text type="secondary">
                  {kind === "edict_auth"
                    ? t("system.externalCreds.edictEmpty")
                    : t("system.externalCreds.engineEmpty")}
                </Typography.Text>
              }
            />
          ),
        }}
      />

      <Modal
        open={modalOpen}
        title={
          kind === "edict_auth" ? t("system.externalCreds.addEdictTitle") : t("system.externalCreds.addEngineTitle")
        }
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={onCreate}>
          <Form.Item name="name" label={t("system.externalCreds.form.name")} rules={[{ required: true }]}>
            <Input
              placeholder={
                kind === "edict_auth" ? t("system.externalCreds.form.namePlaceholderEdict") : t("system.externalCreds.form.namePlaceholderEngine")
              }
            />
          </Form.Item>
          {kind === "edict_auth" ? (
            <>
              <Form.Item
                name="host_pattern"
                label={t("system.externalCreds.form.hostPattern")}
                rules={[{ required: true }]}
              >
                <Input placeholder={t("system.externalCreds.form.hostPlaceholder")} />
              </Form.Item>
              <Form.Item
                name="header_template"
                label={t("system.externalCreds.form.headerTemplate")}
                initialValue="Authorization: Bearer {value}"
                tooltip={t("system.externalCreds.form.headerTemplateTooltip")}
              >
                <Input />
              </Form.Item>
            </>
          ) : (
            <Form.Item
              name="provider_name"
              label={t("system.externalCreds.form.provider")}
              rules={[{ required: true }]}
            >
              <Select
                placeholder={t("system.externalCreds.form.providerPlaceholder")}
                options={[
                  { value: "jina", label: "Jina (web_fetch / web_search)" },
                  { value: "tavily", label: "Tavily (web_search)" },
                  {
                    value: "firecrawl",
                    label: "Firecrawl (web_fetch / web_extract)",
                  },
                ]}
              />
            </Form.Item>
          )}
          <Form.Item name="value" label={t("system.externalCreds.form.value")} rules={[{ required: true }]}>
            <Input.Password placeholder={t("system.externalCreds.form.valuePlaceholder")} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={editOpen}
        title={
          editRow?.kind === "engine_provider" ? t("system.externalCreds.editEngineTitle") : t("system.externalCreds.editCredTitle")
        }
        onCancel={() => setEditOpen(false)}
        onOk={() => editForm.submit()}
        destroyOnClose
      >
        <Form form={editForm} layout="vertical" onFinish={onEditSubmit}>
          {editRow && (
            <div style={{ marginBottom: 12, fontSize: 12, color: "var(--ts-color-text-secondary)" }}>
              <div>
                {t("system.externalCreds.infoLabel")}<code style={monoStyle}>{editRow.name}</code>
              </div>
              {editRow.kind === "engine_provider" ? (
                <div>
                  {t("system.externalCreds.infoProvider")}
                  <code style={monoStyle}>{editRow.provider_name}</code>
                </div>
              ) : (
                <>
                  <div>
                    {t("system.externalCreds.infoHost")}
                    <code style={monoStyle}>{editRow.host_pattern}</code>
                  </div>
                  <div>
                    {t("system.externalCreds.infoHeader")}
                    <code style={monoStyle}>{editRow.header_template}</code>
                  </div>
                </>
              )}
              <div style={{ color: "var(--ts-color-warning)", marginTop: 4 }}>
                {t("system.externalCreds.immutableHint")}
              </div>
            </div>
          )}
          <Form.Item
            name="value"
            label={t("system.externalCreds.form.newValue")}
            tooltip={t("system.externalCreds.form.newValueTooltip")}
          >
            <Input.Password placeholder={t("system.externalCreds.form.newValuePlaceholder")} />
          </Form.Item>
          {editRow?.kind === "edict_auth" && (
            <Form.Item name="extra_headers" label={t("system.externalCreds.form.extraHeaders")}>
              <Input.TextArea
                rows={3}
                placeholder={t("system.externalCreds.form.extraHeadersPlaceholder")}
              />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </Space>
  );
}
