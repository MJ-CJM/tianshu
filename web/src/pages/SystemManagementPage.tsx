import { useState } from "react";
import {
  Tabs,
  Table,
  Tag,
  Button,
  Drawer,
  Input,
  Space,
  Spin,
  Modal,
  Form,
  Popconfirm,
  Segmented,
  Typography,
  notification,
  theme,
} from "antd";
import {
  EditOutlined,
  DeleteOutlined,
  PlusOutlined,
  EyeOutlined,
} from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
import {
  useSkills,
  useSkillDetail,
  useUpdateSkill,
  useCreateSkill,
  useDeleteSkill,
  useTools,
  usePromptFiles,
  usePromptFileContent,
  useUpdatePromptFile,
  usePromptPreview,
} from "../hooks/useSystem";
import { usePersonas } from "../hooks/usePersonas";
import type { SkillInfo, ToolInfo } from "../api/types";

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

  const tierConfig: Record<number, { color: string; label: string }> = {
    0: { color: "green", label: "T0" },
    1: { color: "blue", label: "T1" },
    2: { color: "orange", label: "T2" },
    3: { color: "red", label: "T3" },
  };

  const columns = [
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
  );
}

// ==================== Tab 3: System Prompt ====================

function SystemPromptTab() {
  const { token } = theme.useToken();
  const { data: personas } = usePersonas();
  const { data: promptFiles } = usePromptFiles();
  const [selectedPersona, setSelectedPersona] = useState<string | null>(null);
  const [editingFile, setEditingFile] = useState<{
    personaId: string;
    filename: string;
  } | null>(null);
  const [editContent, setEditContent] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewPersona, setPreviewPersona] = useState<string | null>(null);

  const personaIds = (personas ?? []).map((p) => p.id);
  // Also include "court" if it has prompt files
  const allPersonaIds = promptFiles
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
            onChange={(val) => setSelectedPersona(val as string)}
            options={displayIds.map((id) => {
              const p = (personas ?? []).find((pp) => pp.id === id);
              const label = p ? p.name : id === "court" ? "朝廷" : id;
              return { value: id, label };
            })}
          />
        </div>
      )}

      {activePersona && (
        <>
          <div
            style={{
              marginBottom: 16,
              display: "flex",
              justifyContent: "flex-end",
            }}
          >
            <Button
              icon={<EyeOutlined />}
              onClick={() => handlePreview(activePersona)}
              disabled={activePersona === "court"}
            >
              预览组装 Prompt
            </Button>
          </div>

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
    </>
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
            label: "圣旨模板",
            children: <SystemPromptTab />,
          },
        ]}
      />
    </PageContainer>
  );
}
