import { useState, useEffect, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Tabs,
  Table,
  Tag,
  Button,
  Drawer,
  Input,
  InputNumber,
  Switch,
  Space,
  Spin,
  Modal,
  Form,
  Popconfirm,
  Segmented,
  Typography,
  Collapse,
  Divider,
  notification,
  theme,
  Card,
  Progress,
  Row,
  Col,
  Statistic,
  Alert,
  Empty,
  Select,
  Tooltip,
} from "antd";
import {
  EditOutlined,
  DeleteOutlined,
  PlusOutlined,
  EyeOutlined,
  LockOutlined,
} from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
import MCPTab from "../components/system/MCPTab";
import SecretSkillsTab from "../components/system/SecretSkillsTab";
import {
  useSkills,
  useSkillDetail,
  useUpdateSkill,
  useCreateSkill,
  useDeleteSkill,
  useTools,
  useSetToolEnabled,
  usePromptFiles,
  usePromptFileContent,
  useUpdatePromptFile,
  usePromptPreview,
} from "../hooks/useSystem";
import { useProviders, useDeleteProvider, usePlugins } from "../hooks/useProviders";
import { usePromptLayers } from "../hooks/useOps";
import { usePersonas } from "../hooks/usePersonas";
import {
  useAgentConfig,
  useUpdateAgentConfig,
  useConfigs,
  useCreateConfig,
  useUpdateNamedConfig,
  useDeleteConfig,
  useActivateConfig,
} from "../hooks/useConfig";
import type {
  SkillInfo,
  ToolInfo,
  ProviderInfo,
  PluginInfo,
  LLMConfig,
  LLMConfigCreateRequest,
  LLMConfigUpdateRequest,
  AgentConfigUpdateRequest,
  Credential,
  CredentialCreate,
} from "../api/types";
import {
  listCredentials,
  createCredential,
  deleteCredential,
  updateCredential,
} from "../api/credentials";
import { useT } from "../i18n";

const monoStyle: React.CSSProperties = {
  fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
  fontSize: 13,
};

// ==================== Tab 1: Skills ====================

function SkillsTab() {
  const t = useT();
  const { token } = theme.useToken();
  const { data: skills, isLoading } = useSkills();
  // 技能库只展示原本加载进来的技能；agent 生成的归"习得技能" tab
  const loadedSkills = (skills ?? []).filter((s) => s.created_by !== "agent");
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();

  const { data: detail, isLoading: detailLoading } =
    useSkillDetail(selectedSkill);
  const [editContent, setEditContent] = useState<string>("");
  const [dirty, setDirty] = useState(false);

  const updateMutation = useUpdateSkill();
  const createMutation = useCreateSkill();
  const deleteMutation = useDeleteSkill();

  const handleOpenDetail = (name: string) => {
    setSelectedSkill(name);
    setDirty(false);
  };

  const handleContentChange = (val: string) => {
    setEditContent(val);
    setDirty(true);
  };

  const handleSave = () => {
    if (!selectedSkill) return;
    updateMutation.mutate(
      { name: selectedSkill, content: editContent },
      {
        onSuccess: () => {
          notification.success({ message: t("system.toast.skillSaved") });
          setDirty(false);
        },
      },
    );
  };

  const handleCreate = () => {
    createForm.validateFields().then((values) => {
      createMutation.mutate(
        { name: values.name, content: values.content || "" },
        {
          onSuccess: () => {
            notification.success({ message: t("system.toast.skillCreated", { name: values.name }) });
            setCreateOpen(false);
            createForm.resetFields();
          },
        },
      );
    });
  };

  // Compute char budget stats
  const totalChars = loadedSkills.reduce(
    (acc, s) => acc + s.content_length,
    0,
  );

  const columns = [
    {
      title: t("system.skills.table.name"),
      dataIndex: "name",
      key: "name",
      render: (name: string) => (
        <a onClick={() => handleOpenDetail(name)}>{name}</a>
      ),
    },
    {
      title: t("system.skills.table.description"),
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: t("system.skills.table.source"),
      dataIndex: "source",
      key: "source",
      width: 100,
      render: (source: string) => {
        const colorMap: Record<string, string> = {
          builtin: "blue",
          user: "cyan",
          workspace: "green",
          injected: "purple",
        };
        return <Tag color={colorMap[source] ?? "default"}>{source}</Tag>;
      },
    },
    {
      title: t("system.skills.table.toolTier"),
      dataIndex: "tool_tier",
      key: "tool_tier",
      width: 100,
      render: (v: string | null) => v ?? "-",
    },
    {
      title: t("system.skills.table.chars"),
      dataIndex: "content_length",
      key: "content_length",
      width: 90,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: t("system.skills.table.actions"),
      key: "actions",
      width: 80,
      render: (_: unknown, record: SkillInfo) =>
        record.source === "workspace" || record.source === "user" ? (
          <Popconfirm
            title={t("system.skills.confirmDelete")}
            onConfirm={() =>
              deleteMutation.mutate(record.name, {
                onSuccess: () =>
                  notification.success({
                    message: t("system.toast.skillDeleted", { name: record.name }),
                  }),
              })
            }
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              type="text"
            />
          </Popconfirm>
        ) : null,
    },
  ];

  return (
    <>
      <div
        style={{
          marginBottom: 16,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography.Text style={{ color: token.colorTextSecondary }}>
          {t("system.skills.charsLoaded", { n: totalChars.toLocaleString() })}
        </Typography.Text>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateOpen(true)}
        >
          {t("system.skills.newSkill")}
        </Button>
      </div>

      <Table
        dataSource={loadedSkills}
        columns={columns}
        rowKey="name"
        loading={isLoading}
        size="small"
        pagination={false}
        onRow={(record) => ({
          style: { cursor: "pointer" },
          onClick: () => handleOpenDetail(record.name),
        })}
      />

      {/* Skill Detail Drawer */}
      <Drawer
        title={selectedSkill ? t("system.skills.detailWithName", { name: selectedSkill }) : t("system.skills.detail")}
        open={!!selectedSkill}
        onClose={() => setSelectedSkill(null)}
        width={640}
        extra={
          <Button
            type="primary"
            disabled={!dirty}
            loading={updateMutation.isPending}
            onClick={handleSave}
          >
            {t("button.save")}
          </Button>
        }
      >
        {detailLoading ? (
          <Spin />
        ) : detail ? (
          <div>
            <div style={{ marginBottom: 12 }}>
              <Space>
                <Tag
                  color={
                    detail.source === "builtin"
                      ? "blue"
                      : detail.source === "workspace"
                        ? "green"
                        : "purple"
                  }
                >
                  {detail.source}
                </Tag>
                {detail.always && <Tag color="orange">always</Tag>}
                {detail.tool_tier && (
                  <Tag>tier: {detail.tool_tier}</Tag>
                )}
              </Space>
            </div>
            {detail.description && (
              <Typography.Paragraph
                type="secondary"
                style={{ marginBottom: 12 }}
              >
                {detail.description}
              </Typography.Paragraph>
            )}
            <Input.TextArea
              value={dirty ? editContent : detail.content}
              onChange={(e) => handleContentChange(e.target.value)}
              onFocus={() => {
                if (!dirty) setEditContent(detail.content);
              }}
              autoSize={{ minRows: 20, maxRows: 40 }}
              style={monoStyle}
            />
          </div>
        ) : (
          <Typography.Text type="secondary">{t("system.skills.notFound")}</Typography.Text>
        )}
      </Drawer>

      {/* Create Skill Modal */}
      <Modal
        title={t("system.skills.createTitle")}
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => {
          setCreateOpen(false);
          createForm.resetFields();
        }}
        confirmLoading={createMutation.isPending}
        okText={t("action.create")}
        cancelText={t("common.cancel")}
      >
        <Form form={createForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="name"
            label={t("system.skills.form.name")}
            rules={[
              { required: true, message: t("system.skills.form.nameRequired") },
              {
                pattern: /^[a-zA-Z0-9_-]+$/,
                message: t("system.skills.form.namePattern"),
              },
            ]}
          >
            <Input placeholder={t("system.skills.form.namePlaceholder")} />
          </Form.Item>
          <Form.Item name="content" label={t("system.skills.form.content")}>
            <Input.TextArea
              rows={12}
              placeholder={t("system.skills.form.contentPlaceholder")}
              style={monoStyle}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

// ==================== Tab 2: Tools ====================

function ToolsTab() {
  const t = useT();
  const { data: tools, isLoading } = useTools();
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const setEnabledMutation = useSetToolEnabled();

  const handleToggle = (name: string, enabled: boolean) => {
    setEnabledMutation.mutate(
      { name, enabled },
      {
        onSuccess: () => {
          notification.success({
            message: enabled ? t("system.toast.toolEnabled", { name }) : t("system.toast.toolDisabled", { name }),
          });
        },
        onError: (err: any) => {
          notification.error({
            message: t("system.toast.actionFailed"),
            description: String(err?.response?.data?.detail ?? err?.message ?? err),
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
      <Alert
        type="info"
        showIcon
        message={t("system.tools.liveAlert")}
      />
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

// ==================== Prompt Layers ====================

function PromptLayersCard({
  personaId,
  deptId,
  title,
  onEditFile,
}: {
  personaId: string | null;
  deptId?: string;
  title?: string;
  onEditFile?: (personaId: string, filename: string) => void;
}) {
  const t = useT();
  const { data: layers, isLoading } = usePromptLayers(personaId);

  if (!personaId || !layers) return null;

  // Map layer names to editable file targets
  const editableMap: Record<string, { pid: string; filename: string }> = {
    "COURT.md": { pid: "court", filename: "COURT.md" },
    "Court MEMORY.md": { pid: "court", filename: "MEMORY.md" },
    ...(deptId ? {
      "SOUL.md": { pid: deptId, filename: "SOUL.md" },
      "ROLE.md": { pid: deptId, filename: "ROLE.md" },
      "MEMORY.md": { pid: deptId, filename: "MEMORY.md" },
    } : {}),
  };

  return (
    <Card title={title ?? t("system.prompt.layeredAnalysis")} size="small" loading={isLoading} style={{ marginTop: 16 }}>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Statistic title={t("system.prompt.totalChars")} value={layers.total_chars} />
        </Col>
        <Col span={8}>
          <Statistic title={t("system.prompt.estTokens")} value={layers.total_tokens_est} />
        </Col>
        <Col span={8}>
          <Statistic title={t("system.prompt.layerCount")} value={layers.layers.length} />
        </Col>
      </Row>
      <Table
        columns={[
          { title: t("system.prompt.table.layer"), dataIndex: "layer", key: "layer", width: 60, align: "center" as const },
          { title: t("system.prompt.table.name"), dataIndex: "name", key: "name", width: 150 },
          { title: t("system.prompt.table.source"), dataIndex: "source", key: "source", ellipsis: true,
            render: (v: string) => <Typography.Text style={{ fontSize: 12 }}>{v}</Typography.Text> },
          { title: t("system.prompt.table.chars"), dataIndex: "chars", key: "chars", width: 80, align: "right" as const },
          { title: t("system.prompt.table.tokensEst"), dataIndex: "tokens_est", key: "tokens_est", width: 100, align: "right" as const },
          { title: t("system.prompt.table.percent"), key: "percent", width: 120,
            render: (_: unknown, record: { chars: number; name: string }) => (
              <Progress
                percent={Math.round((record.chars / (layers.total_chars || 1)) * 100)}
                size="small"
                strokeColor={record.chars > 5000 ? "#faad14" : "#1890ff"}
              />
            ),
          },
          ...(onEditFile ? [{
            title: t("system.prompt.table.actions") as string,
            key: "actions" as const,
            width: 70,
            align: "center" as const,
            render: (_: unknown, record: { chars: number; name: string }) => {
              const target = editableMap[record.name];
              if (!target) return null;
              return (
                <Button
                  type="text"
                  size="small"
                  icon={<EditOutlined />}
                  onClick={() => onEditFile(target.pid, target.filename)}
                />
              );
            },
          }] : []),
        ]}
        dataSource={layers.layers.map((l) => ({ key: l.layer, ...l }))}
        size="small"
        pagination={false}
      />
    </Card>
  );
}

// ==================== Tab 3: System Prompt ====================

function SystemPromptTab() {
  const t = useT();
  const { token } = theme.useToken();
  const { data: personas } = usePersonas();
  const { data: promptData } = usePromptFiles();
  const [selectedPersona, setSelectedPersona] = useState<string | null>(null);
  const [editingFile, setEditingFile] = useState<{
    personaId: string;
    filename: string;
  } | null>(null);
  const [editContent, setEditContent] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewPersona, setPreviewPersona] = useState<string | null>(null);
  const [selectedOfficer, setSelectedOfficer] = useState<string | null>(null);

  const promptFiles = promptData?.files ?? [];
  const departments = promptData?.departments ?? {};

  const personaIds = (personas ?? []).map((p) => p.id);
  // Also include "court" if it has prompt files
  const allPersonaIds = promptFiles.length > 0
    ? [...new Set(promptFiles.map((f) => f.persona_id))]
    : [];
  const displayIds =
    allPersonaIds.length > 0
      ? allPersonaIds
      : personaIds.length > 0
        ? personaIds
        : [];

  const activePersona = selectedPersona ?? displayIds[0] ?? null;

  const { data: fileContent, isLoading: contentLoading } =
    usePromptFileContent(
      editingFile?.personaId ?? null,
      editingFile?.filename ?? null,
    );
  const updateMutation = useUpdatePromptFile();
  const { data: previewData, isLoading: previewLoading } = usePromptPreview(
    previewOpen ? previewPersona : null,
  );

  const personaFiles = (promptFiles ?? []).filter(
    (f) => f.persona_id === activePersona,
  );

  const handleEdit = (personaId: string, filename: string) => {
    setEditingFile({ personaId, filename });
    setEditContent("");
  };

  const handleSave = () => {
    if (!editingFile) return;
    updateMutation.mutate(
      {
        personaId: editingFile.personaId,
        filename: editingFile.filename,
        content: editContent,
      },
      {
        onSuccess: () => {
          notification.success({ message: t("system.toast.fileSaved") });
          setEditingFile(null);
        },
      },
    );
  };

  const handlePreview = (personaId: string) => {
    setPreviewPersona(personaId);
    setPreviewOpen(true);
  };

  return (
    <>
      {displayIds.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <Segmented
            value={activePersona ?? ""}
            onChange={(val) => { setSelectedPersona(val as string); setSelectedOfficer(null); }}
            options={displayIds.map((id) => {
              const label = departments[id] ?? id;
              return { value: id, label };
            })}
          />
        </div>
      )}

      {activePersona && (
        <>
          {/* Template files */}
          <Typography.Text
            type="secondary"
            style={{ display: "block", marginBottom: 8, fontSize: 12 }}
          >
            {t("system.prompt.templateFiles")}
          </Typography.Text>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
            {personaFiles.length === 0 ? (
              <Typography.Text type="secondary">
                {t("system.prompt.emptyFiles")}
              </Typography.Text>
            ) : (
              personaFiles.map((f) => (
                <div
                  key={f.filename}
                  style={{
                    border: `1px solid ${token.colorBorder}`,
                    borderRadius: token.borderRadius,
                    padding: 16,
                    width: 280,
                    background: token.colorBgContainer,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      marginBottom: 8,
                    }}
                  >
                    <Typography.Text strong>{f.filename}</Typography.Text>
                    <Button
                      size="small"
                      icon={<EditOutlined />}
                      onClick={() => handleEdit(f.persona_id, f.filename)}
                    />
                  </div>
                  <Typography.Text
                    type="secondary"
                    style={{ fontSize: 12 }}
                  >
                    {f.size} bytes
                  </Typography.Text>
                </div>
              ))
            )}
          </div>

          {/* Personas belonging to this department */}
          {activePersona !== "court" && (() => {
            const deptPersonas = (personas ?? []).filter(
              (p) => p.department === activePersona,
            );
            if (deptPersonas.length === 0) return null;
            return (
              <div style={{ marginTop: 24 }}>
                <Typography.Text
                  type="secondary"
                  style={{ display: "block", marginBottom: 8, fontSize: 12 }}
                >
                  {t("system.prompt.usingTemplate")}
                </Typography.Text>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
                  {deptPersonas.map((p) => {
                    const isSelected = selectedOfficer === p.id;
                    return (
                      <div
                        key={p.id}
                        onClick={() => setSelectedOfficer(isSelected ? null : p.id)}
                        style={{
                          border: `1px solid ${isSelected ? token.colorPrimary : token.colorBorder}`,
                          borderRadius: token.borderRadius,
                          padding: "10px 16px",
                          background: isSelected ? token.colorPrimaryBg : token.colorBgContainer,
                          display: "flex",
                          alignItems: "center",
                          gap: 12,
                          cursor: "pointer",
                          transition: "all 0.2s",
                        }}
                      >
                        <Typography.Text strong>{p.name}</Typography.Text>
                        <Tag style={{ marginRight: 0 }}>{p.id}</Tag>
                        <Button
                          size="small"
                          icon={<EyeOutlined />}
                          onClick={(e) => { e.stopPropagation(); handlePreview(p.id); }}
                        >
                          {t("system.prompt.preview")}
                        </Button>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}
        </>
      )}

      {/* Edit Drawer */}
      <Drawer
        title={
          editingFile
            ? t("system.prompt.editFileTitle", { personaId: editingFile.personaId, filename: editingFile.filename })
            : t("system.prompt.editFile")
        }
        open={!!editingFile}
        onClose={() => setEditingFile(null)}
        width={640}
        extra={
          <Button
            type="primary"
            loading={updateMutation.isPending}
            onClick={handleSave}
          >
            {t("button.save")}
          </Button>
        }
      >
        {contentLoading ? (
          <Spin />
        ) : (
          <Input.TextArea
            value={editContent || fileContent?.content || ""}
            onChange={(e) => setEditContent(e.target.value)}
            onFocus={() => {
              if (!editContent && fileContent?.content) {
                setEditContent(fileContent.content);
              }
            }}
            autoSize={{ minRows: 20, maxRows: 40 }}
            style={monoStyle}
          />
        )}
      </Drawer>

      {/* Preview Modal */}
      <Modal
        title={t("system.prompt.previewModalTitle", { persona: previewPersona ?? "" })}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={800}
      >
        {previewLoading ? (
          <Spin />
        ) : previewData?.prompt ? (
          <Input.TextArea
            value={previewData.prompt}
            readOnly
            autoSize={{ minRows: 20, maxRows: 40 }}
            style={monoStyle}
          />
        ) : (
          <Typography.Text type="secondary">{t("system.prompt.previewEmpty")}</Typography.Text>
        )}
      </Modal>

      {activePersona !== "court" && (
        <PromptLayersCard
          personaId={selectedOfficer ?? activePersona}
          deptId={activePersona ?? undefined}
          title={
            selectedOfficer
              ? t("system.prompt.layeredFor", { name: (personas ?? []).find((p) => p.id === selectedOfficer)?.name ?? selectedOfficer })
              : t("system.prompt.layeredAnalysis")
          }
          onEditFile={handleEdit}
        />
      )}
    </>
  );
}

// ==================== Tab 4: Providers + LLM Config ====================

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

function ProvidersTab() {
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
        const miss = r.cost_per_1k_prompt;
        const hit = r.cost_per_1k_cache_read;
        const out = r.cost_per_1k_completion;
        const customCount = [miss, hit, out].filter((v) => v != null).length;
        const tooltip =
          customCount === 0
            ? t("system.providers.costNoCustom")
            : customCount === 3
            ? t("system.providers.costAllCustom")
            : t("system.providers.costPartial");
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
            name="model"
            label={t("system.providers.form.model")}
            rules={[{ required: true, message: t("system.providers.form.modelRequired") }]}
          >
            <Input placeholder={t("system.providers.form.modelPlaceholder")} />
          </Form.Item>
          <Form.Item name="api_key" label="API Key">
            <Input.Password placeholder={t("system.providers.form.apiKeyPlaceholder")} />
          </Form.Item>
          <Form.Item name="api_base" label="API Base">
            <Input placeholder={t("system.providers.form.apiBasePlaceholder")} />
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
    </>
  );
}

// ==================== Tab 5: Plugins ====================

function PluginsTab() {
  const t = useT();
  const { data: plugins, isLoading } = usePlugins();

  const columns: import("antd/es/table").ColumnsType<PluginInfo> = [
    { title: t("system.plugins.table.name"), dataIndex: "name", key: "name", width: 160 },
    { title: t("system.plugins.table.version"), dataIndex: "version", key: "version", width: 100 },
    {
      title: t("system.plugins.table.status"), dataIndex: "status", key: "status", width: 100,
      render: (v: string) => <Tag color={v === "active" ? "green" : "default"}>{v}</Tag>,
    },
    {
      title: t("system.plugins.table.sha256"), dataIndex: "sha256", key: "sha256", ellipsis: true,
      render: (v: string | null) => v ? <Typography.Text style={{ fontSize: 11 }} copyable>{v}</Typography.Text> : "—",
    },
    {
      title: t("system.plugins.table.installedAt"), dataIndex: "installed_at", key: "installed_at", width: 180,
      render: (v: string) => (v ? new Date(v).toLocaleString("zh-CN") : "—"),
    },
  ];

  return (
    <Table<PluginInfo>
      columns={columns}
      dataSource={plugins ?? []}
      rowKey="name"
      loading={isLoading}
      size="small"
      pagination={false}
      locale={{ emptyText: t("system.plugins.empty") }}
    />
  );
}

// ==================== Tab 6: Global Config ====================

function GlobalConfigTab() {
  const t = useT();
  const { token } = theme.useToken();
  const { data: agentConfigData } = useAgentConfig();
  const updateAgentMutation = useUpdateAgentConfig();
  const [agentForm, setAgentForm] = useState<AgentConfigUpdateRequest>({});

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

  const handleApply = useCallback(() => {
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
      notification.info({ message: t("system.toast.noChanges") });
      return;
    }
    updateAgentMutation.mutate(payload, {
      onSuccess: (data) => {
        notification.success({ message: t("system.toast.agentParamsUpdated") });
        setAgentForm({ ...data });
      },
    });
  }, [agentForm, agentConfigData, updateAgentMutation, t]);

  const labelStyle: React.CSSProperties = {
    marginBottom: 4,
    fontSize: 13,
    color: token.colorTextTertiary,
  };

  return (
    <Row gutter={16}>
      <Col xs={24} md={12} lg={8}>
        <Card title={t("system.globalConfig.agentSection")} size="small">
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>{t("system.globalConfig.agentMaxIter")}</div>
            <InputNumber
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
              style={{ width: "100%" }}
            />
          </div>
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>{t("system.globalConfig.agentTimeout")}</div>
            <InputNumber
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
              style={{ width: "100%" }}
            />
          </div>
        </Card>
      </Col>
      <Col xs={24} md={12} lg={8}>
        <Card title={t("system.globalConfig.skillSection")} size="small">
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>{t("system.globalConfig.skillCharBudget")}</div>
            <InputNumber
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
              style={{ width: "100%" }}
            />
          </div>
        </Card>
      </Col>
      <Col xs={24} lg={8} style={{ display: "flex", alignItems: "flex-start", paddingTop: 38 }}>
        <Button
          type="primary"
          loading={updateAgentMutation.isPending}
          onClick={handleApply}
        >
          {t("system.globalConfig.apply")}
        </Button>
      </Col>
    </Row>
  );
}

// ==================== Tab: External Credentials ====================

function ExternalCredentialsTab() {
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
    } catch (e: any) {
      const status = e?.response?.status;
      const detail: string = String(
        e?.response?.data?.detail ?? e?.message ?? e,
      );
      // 503 + vault unavailable → 引导页（非错误提示）
      if (status === 503 && /vault.*unavailable|MASTER_KEY/i.test(detail)) {
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
    } catch (e: any) {
      notification.error({
        message: t("system.toast.credCreateFailed"),
        description: String(e?.response?.data?.detail ?? e?.message ?? e),
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
    } catch (e: any) {
      notification.error({
        message: t("system.toast.credDeleteFailed"),
        description: String(e?.response?.data?.detail ?? e?.message ?? e),
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
    } catch (e: any) {
      notification.error({
        message: t("system.toast.credSaveFailed"),
        description: String(e?.response?.data?.detail ?? e?.message ?? e),
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
    } catch (e: any) {
      notification.error({
        message: t("system.toast.credToggleFailed"),
        description: String(e?.response?.data?.detail ?? e?.message ?? e),
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
            <div style={{ marginBottom: 12, fontSize: 12, color: "#999" }}>
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
              <div style={{ color: "#faad14", marginTop: 4 }}>
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

// ==================== Main Page ====================

export default function SystemManagementPage() {
  const t = useT();
  return (
    <PageContainer title={t("system.title")}>
      <Tabs
        defaultActiveKey="skills"
        items={[
          {
            key: "skills",
            label: t("system.tab.skills"),
            children: <SkillsTab />,
          },
          {
            key: "secret-skills",
            label: t("system.tab.secretSkills"),
            children: <SecretSkillsTab />,
          },
          {
            key: "tools",
            label: t("system.tab.tools"),
            children: <ToolsTab />,
          },
          {
            key: "mcp",
            label: t("system.tab.mcp"),
            children: <MCPTab />,
          },
          {
            key: "prompt",
            label: t("system.tab.prompt"),
            children: <SystemPromptTab />,
          },
          {
            key: "providers",
            label: t("system.tab.providers"),
            children: <ProvidersTab />,
          },
          {
            key: "plugins",
            label: t("system.tab.plugins"),
            children: <PluginsTab />,
          },
          {
            key: "config",
            label: t("system.tab.config"),
            children: <GlobalConfigTab />,
          },
          {
            key: "external-creds",
            label: t("system.tab.externalCreds"),
            children: <ExternalCredentialsTab />,
          },
        ]}
      />
    </PageContainer>
  );
}
