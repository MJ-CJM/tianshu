import { useState, useEffect, useCallback } from "react";
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
} from "antd";
import {
  EditOutlined,
  DeleteOutlined,
  PlusOutlined,
  EyeOutlined,
  LockOutlined,
} from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
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
} from "../api/credentials";

const monoStyle: React.CSSProperties = {
  fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
  fontSize: 13,
};

// ==================== Tab 1: Skills ====================

function SkillsTab() {
  const { token } = theme.useToken();
  const { data: skills, isLoading } = useSkills();
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
          notification.success({ message: "技能已保存" });
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
            notification.success({ message: `技能 "${values.name}" 已创建` });
            setCreateOpen(false);
            createForm.resetFields();
          },
        },
      );
    });
  };

  // Compute char budget stats
  const totalChars = (skills ?? []).reduce(
    (acc, s) => acc + s.content_length,
    0,
  );

  const columns = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      render: (name: string) => (
        <a onClick={() => handleOpenDetail(name)}>{name}</a>
      ),
    },
    {
      title: "描述",
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: "来源",
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
      title: "工具等级",
      dataIndex: "tool_tier",
      key: "tool_tier",
      width: 100,
      render: (v: string | null) => v ?? "-",
    },
    {
      title: "字符数",
      dataIndex: "content_length",
      key: "content_length",
      width: 90,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: "操作",
      key: "actions",
      width: 80,
      render: (_: unknown, record: SkillInfo) =>
        record.source === "workspace" || record.source === "user" ? (
          <Popconfirm
            title="确认删除此技能？"
            onConfirm={() =>
              deleteMutation.mutate(record.name, {
                onSuccess: () =>
                  notification.success({
                    message: `技能 "${record.name}" 已删除`,
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
          已加载 {totalChars.toLocaleString()} chars
        </Typography.Text>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateOpen(true)}
        >
          新建技能
        </Button>
      </div>

      <Table
        dataSource={skills}
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
        title={selectedSkill ? `技能: ${selectedSkill}` : "技能详情"}
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
            保存
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
          <Typography.Text type="secondary">未找到技能</Typography.Text>
        )}
      </Drawer>

      {/* Create Skill Modal */}
      <Modal
        title="新建技能"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => {
          setCreateOpen(false);
          createForm.resetFields();
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
        cancelText="取消"
      >
        <Form form={createForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="name"
            label="技能名称"
            rules={[
              { required: true, message: "请输入技能名称" },
              {
                pattern: /^[a-zA-Z0-9_-]+$/,
                message: "仅支持字母、数字、下划线、连字符",
              },
            ]}
          >
            <Input placeholder="例如 my-skill" />
          </Form.Item>
          <Form.Item name="content" label="SKILL.md 内容">
            <Input.TextArea
              rows={12}
              placeholder="在此输入 SKILL.md 内容..."
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
  const { data: tools, isLoading } = useTools();
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const setEnabledMutation = useSetToolEnabled();

  const handleToggle = (name: string, enabled: boolean) => {
    setEnabledMutation.mutate(
      { name, enabled },
      {
        onSuccess: () => {
          notification.success({
            message: enabled ? `已启用 ${name}` : `已禁用 ${name}`,
          });
        },
        onError: (err: any) => {
          notification.error({
            message: "操作失败",
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
      title: "启用",
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
      title: "名称",
      dataIndex: "name",
      key: "name",
      width: 180,
      render: (name: string) => (
        <Typography.Text strong>{name}</Typography.Text>
      ),
    },
    {
      title: "描述",
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: "等级",
      dataIndex: "tier",
      key: "tier",
      width: 70,
      render: (tier: number) => {
        const cfg = tierConfig[tier] ?? { color: "default", label: `T${tier}` };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: "关联 Persona",
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
        message="工具启用开关 live 生效 — 关闭后 LLM 在下一轮调用里看不到此工具；无需重启。"
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
    <Card title={title ?? "Prompt 分层分析"} size="small" loading={isLoading} style={{ marginTop: 16 }}>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Statistic title="总字符数" value={layers.total_chars} />
        </Col>
        <Col span={8}>
          <Statistic title="估算 Token" value={layers.total_tokens_est} />
        </Col>
        <Col span={8}>
          <Statistic title="层数" value={layers.layers.length} />
        </Col>
      </Row>
      <Table
        columns={[
          { title: "层", dataIndex: "layer", key: "layer", width: 60, align: "center" as const },
          { title: "名称", dataIndex: "name", key: "name", width: 150 },
          { title: "来源", dataIndex: "source", key: "source", ellipsis: true,
            render: (v: string) => <Typography.Text style={{ fontSize: 12 }}>{v}</Typography.Text> },
          { title: "字符", dataIndex: "chars", key: "chars", width: 80, align: "right" as const },
          { title: "Token (est)", dataIndex: "tokens_est", key: "tokens_est", width: 100, align: "right" as const },
          { title: "占比", key: "percent", width: 120,
            render: (_: unknown, record: { chars: number; name: string }) => (
              <Progress
                percent={Math.round((record.chars / (layers.total_chars || 1)) * 100)}
                size="small"
                strokeColor={record.chars > 5000 ? "#faad14" : "#1890ff"}
              />
            ),
          },
          ...(onEditFile ? [{
            title: "操作" as const,
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
          notification.success({ message: "文件已保存" });
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
            部门模板文件
          </Typography.Text>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
            {personaFiles.length === 0 ? (
              <Typography.Text type="secondary">
                暂无 Prompt 文件
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
                  使用此模板的官员（点击查看/编辑 Prompt 分层）
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
                          预览
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
            ? `编辑: ${editingFile.personaId}/${editingFile.filename}`
            : "编辑文件"
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
            保存
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
        title={`System Prompt 预览: ${previewPersona ?? ""}`}
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
          <Typography.Text type="secondary">无法生成预览</Typography.Text>
        )}
      </Modal>

      {activePersona !== "court" && (
        <PromptLayersCard
          personaId={selectedOfficer ?? activePersona}
          deptId={activePersona ?? undefined}
          title={
            selectedOfficer
              ? `${(personas ?? []).find((p) => p.id === selectedOfficer)?.name ?? selectedOfficer} — Prompt 分层`
              : "Prompt 分层分析"
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

function ProvidersTab() {
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
          const msg = err instanceof Error ? err.message : "名称已存在";
          notification.error({ message: msg });
        },
      });
    });
  }, [addForm, createMutation]);

  const handleDeleteProvider = (name: string) => {
    deleteProviderMutation.mutate(name, {
      onSuccess: () => notification.success({ message: `Provider "${name}" 已删除` }),
    });
  };

  const configs = configsData?.configs ?? [];
  const activeName = configsData?.active_name ?? "";

  const providerColumns: import("antd/es/table").ColumnsType<ProviderInfo> = [
    { title: "名称", dataIndex: "name", key: "name", width: 140 },
    { title: "模型", dataIndex: "model", key: "model", width: 160 },
    {
      title: "状态", dataIndex: "status", key: "status", width: 100,
      render: (v: string) => {
        const color = v === "active" ? "green" : v === "degraded" ? "orange" : "red";
        return <Tag color={color}>{v}</Tag>;
      },
    },
    { title: "优先级", dataIndex: "priority", key: "priority", width: 80, align: "right" },
    {
      title: "RPM", dataIndex: "rpm_limit", key: "rpm_limit", width: 80, align: "right",
      render: (v: number | null) => v ?? "—",
    },
    {
      title: "Prompt ¥/1K", dataIndex: "cost_per_1k_prompt", key: "cost",
      width: 110, align: "right",
      render: (v: number | null) => (v != null ? `¥${v.toFixed(4)}` : "—"),
    },
    {
      title: "", key: "actions", width: 50,
      render: (_, record) => (
        <Popconfirm title="确定删除？" onConfirm={() => handleDeleteProvider(record.name)}>
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
              活跃
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
              notification.success({ message: `已切换活跃配置为 "${c.name}"` }),
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
    <>
      {/* Provider 列表 */}
      <Typography.Title level={5} style={{ marginBottom: 12 }}>Provider 列表</Typography.Title>
      <Table<ProviderInfo>
        columns={providerColumns}
        dataSource={providers ?? []}
        rowKey="name"
        loading={providersLoading}
        size="small"
        pagination={false}
        locale={{ emptyText: "暂无 Provider" }}
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
        <Typography.Title level={5} style={{ margin: 0 }}>LLM 配置</Typography.Title>
        <Button
          type="primary"
          size="small"
          icon={<PlusOutlined />}
          onClick={() => setAddModalOpen(true)}
        >
          添加配置
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
    </>
  );
}

// ==================== Tab 5: Plugins ====================

function PluginsTab() {
  const { data: plugins, isLoading } = usePlugins();

  const columns: import("antd/es/table").ColumnsType<PluginInfo> = [
    { title: "名称", dataIndex: "name", key: "name", width: 160 },
    { title: "版本", dataIndex: "version", key: "version", width: 100 },
    {
      title: "状态", dataIndex: "status", key: "status", width: 100,
      render: (v: string) => <Tag color={v === "active" ? "green" : "default"}>{v}</Tag>,
    },
    {
      title: "SHA256", dataIndex: "sha256", key: "sha256", ellipsis: true,
      render: (v: string | null) => v ? <Typography.Text style={{ fontSize: 11 }} copyable>{v}</Typography.Text> : "—",
    },
    {
      title: "安装时间", dataIndex: "installed_at", key: "installed_at", width: 180,
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
      locale={{ emptyText: "暂无插件" }}
    />
  );
}

// ==================== Tab 6: Global Config ====================

function GlobalConfigTab() {
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

  const labelStyle: React.CSSProperties = {
    marginBottom: 4,
    fontSize: 13,
    color: token.colorTextTertiary,
  };

  return (
    <Row gutter={16}>
      <Col xs={24} md={12} lg={8}>
        <Card title="Agent 参数" size="small">
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>最大迭代次数</div>
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
            <div style={labelStyle}>执行超时 (秒)</div>
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
        <Card title="Skill 参数" size="small">
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>字符预算</div>
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
          应用
        </Button>
      </Col>
    </Row>
  );
}

// ==================== Tab: External Credentials ====================

function ExternalCredentialsTab() {
  const [kind, setKind] = useState<"edict_auth" | "engine_provider">(
    "edict_auth",
  );
  const [items, setItems] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [vaultUnavailable, setVaultUnavailable] = useState(false);
  const [form] = Form.useForm();

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
          message: "加载凭证失败",
          description: detail,
        });
      }
    } finally {
      setLoading(false);
    }
  }, [kind]);

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
        message: kind === "edict_auth" ? "凭证已创建" : "已创建，重启后台生效",
      });
      setModalOpen(false);
      form.resetFields();
      reload();
    } catch (e: any) {
      notification.error({
        message: "创建失败",
        description: String(e?.response?.data?.detail ?? e?.message ?? e),
      });
    }
  };

  const onDelete = async (id: string) => {
    try {
      await deleteCredential(id);
      notification.success({ message: "凭证已删除" });
      reload();
    } catch (e: any) {
      notification.error({
        message: "删除失败",
        description: String(e?.response?.data?.detail ?? e?.message ?? e),
      });
    }
  };

  const edictColumns = [
    { title: "名称", dataIndex: "name", key: "name" },
    {
      title: "匹配域",
      dataIndex: "host_pattern",
      key: "host_pattern",
      render: (v: string) => <code style={monoStyle}>{v}</code>,
    },
    {
      title: "Header 模板",
      dataIndex: "header_template",
      key: "header_template",
      render: (v: string) => (
        <code style={monoStyle}>{v.replace(/\{value\}/, "•••")}</code>
      ),
    },
    {
      title: "最近使用",
      dataIndex: "last_used_at",
      key: "last_used_at",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, row: Credential) => (
        <Popconfirm
          title="删除此凭证？引用它的 Edict 将无法再注入 header。"
          onConfirm={() => onDelete(row.id)}
        >
          <Button size="small" danger icon={<DeleteOutlined />}>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  const providerColumns = [
    { title: "名称", dataIndex: "name", key: "name" },
    {
      title: "Provider",
      dataIndex: "provider_name",
      key: "provider_name",
      render: (v: string | null) =>
        v ? <Tag color="geekblue">{v}</Tag> : "—",
    },
    {
      title: "最近使用",
      dataIndex: "last_used_at",
      key: "last_used_at",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, row: Credential) => (
        <Popconfirm
          title="删除此 provider key？重启后对应引擎降级（回落 env 或关闭）。"
          onConfirm={() => onDelete(row.id)}
        >
          <Button size="small" danger icon={<DeleteOutlined />}>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  if (vaultUnavailable) {
    return (
      <Alert
        type="warning"
        showIcon
        icon={<LockOutlined />}
        message="尚未配置主密钥 TIANSHU_SECRET_MASTER_KEY"
        description={
          <Space direction="vertical" style={{ width: "100%", marginTop: 4 }}>
            <Typography.Paragraph style={{ marginBottom: 4 }}>
              外部凭证用 Fernet 对称加密存储，需要先给系统配置一把主密钥才能启用。
              密钥丢失等于所有已存凭证不可恢复，请妥善备份。
            </Typography.Paragraph>
            <Typography.Text strong>配置步骤</Typography.Text>
            <Typography.Paragraph style={{ marginBottom: 4 }}>
              1) 生成一把 Fernet 密钥：
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
              2) 把输出写进项目根目录的 <code style={monoStyle}>.env</code>：
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
{`TIANSHU_SECRET_MASTER_KEY=<上一步生成的 key>`}
            </pre>
            <Typography.Paragraph style={{ marginBottom: 0, marginTop: 8 }}>
              3) 重启天枢后台进程 → 刷新本页。其它已注册的网络工具
              (<code style={monoStyle}>web_fetch</code> / <code style={monoStyle}>web_search</code>
              / <code style={monoStyle}>web_extract</code>) 不受影响。
            </Typography.Paragraph>
            <Space style={{ marginTop: 12 }}>
              <Button type="primary" onClick={reload}>
                我已配置，重新检测
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
            { value: "edict_auth", label: "Edict 凭证" },
            { value: "engine_provider", label: "引擎配置" },
          ]}
        />
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setModalOpen(true)}
        >
          新增
        </Button>
      </Space>

      {kind === "engine_provider" && (
        <Alert
          type="info"
          message="引擎配置用于给 web_fetch / web_search / web_extract 使用的 Jina / Tavily / Firecrawl 服务传入 API Key。"
          description="DB 里配置后优先使用；没有则回落 .env；修改后需要重启后台生效。"
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
                    ? "还没添加任何外部凭证。点右上角「新增」开始。"
                    : "还没配置任何引擎 Provider key。"}
                </Typography.Text>
              }
            />
          ),
        }}
      />

      <Modal
        open={modalOpen}
        title={
          kind === "edict_auth" ? "新增外部凭证" : "新增引擎 Provider Key"
        }
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={onCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input
              placeholder={
                kind === "edict_auth" ? "github-prod-token" : "jina-prod"
              }
            />
          </Form.Item>
          {kind === "edict_auth" ? (
            <>
              <Form.Item
                name="host_pattern"
                label="匹配域 (例: api.github.com 或 *.notion.com)"
                rules={[{ required: true }]}
              >
                <Input placeholder="api.github.com" />
              </Form.Item>
              <Form.Item
                name="header_template"
                label="Header 模板"
                initialValue="Authorization: Bearer {value}"
                tooltip="用 {value} 做占位符"
              >
                <Input />
              </Form.Item>
            </>
          ) : (
            <Form.Item
              name="provider_name"
              label="Provider"
              rules={[{ required: true }]}
            >
              <Select
                placeholder="选择服务提供方"
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
          <Form.Item name="value" label="Value" rules={[{ required: true }]}>
            <Input.Password placeholder="不会回显" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

// ==================== Main Page ====================

export default function SystemManagementPage() {
  return (
    <PageContainer title="藏兵阁">
      <Tabs
        defaultActiveKey="skills"
        items={[
          {
            key: "skills",
            label: "技能库",
            children: <SkillsTab />,
          },
          {
            key: "tools",
            label: "工具箱",
            children: <ToolsTab />,
          },
          {
            key: "prompt",
            label: "Prompt 模板",
            children: <SystemPromptTab />,
          },
          {
            key: "providers",
            label: "模型供应",
            children: <ProvidersTab />,
          },
          {
            key: "plugins",
            label: "插件",
            children: <PluginsTab />,
          },
          {
            key: "config",
            label: "全局配置",
            children: <GlobalConfigTab />,
          },
          {
            key: "external-creds",
            label: "外部凭证",
            children: <ExternalCredentialsTab />,
          },
        ]}
      />
    </PageContainer>
  );
}
