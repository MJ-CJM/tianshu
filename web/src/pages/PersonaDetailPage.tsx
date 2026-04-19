import { useState, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Tabs,
  Tag,
  Space,
  Typography,
  Descriptions,
  Spin,
  Empty,
  Button,
  Drawer,
  Input,
  InputNumber,
  Switch,
  Select,
  Modal,
  Form,
  Table,
  Card,
  Collapse,
  Popconfirm,
  Row,
  Col,
  Statistic,
  Progress,
  notification,
  theme,
} from "antd";
import {
  ArrowLeftOutlined,
  EditOutlined,
  EyeOutlined,
  FileTextOutlined,
  HistoryOutlined,
  UserOutlined,
  DeleteOutlined,
  SearchOutlined,
  TrophyOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import {
  usePersonas,
  usePersonaMetrics,
  useUpdatePersona,
} from "../hooks/usePersonas";
import {
  usePromptFiles,
  usePromptFileContent,
  useUpdatePromptFile,
  usePromptPreview,
  useTools,
  useSkills,
} from "../hooks/useSystem";
import { usePromptLayers } from "../hooks/useOps";
import {
  usePersonaMemorials,
  usePersonaMemory,
  useDeleteMemory,
  useRecallMemory,
} from "../hooks/useMemory";
import { useDepartments } from "../hooks/useDepartments";
import ProfileTab from "../components/persona/ProfileTab";
import { useConfigs } from "../hooks/useConfig";
import type {
  PersonaInfo,
  PersonaUpdateRequest,
  MemoryEntry,
  EdictMemorialGroup,
  MemorialBrief,
  PromptFileInfo,
} from "../api/types";

const { Text, Paragraph } = Typography;

const monoStyle: React.CSSProperties = {
  fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
  fontSize: 13,
};

const categoryColors: Record<string, string> = {
  observation: "blue",
  insight: "gold",
  entity: "green",
  summary: "purple",
};

const sourceColors: Record<string, string> = {
  agent: "default",
  compaction: "cyan",
  reflection: "orange",
};

const statusColors: Record<string, string> = {
  completed: "green",
  failed: "red",
  running: "blue",
  submitted: "default",
  cancelled: "orange",
  open: "blue",
};

// ==================== Tab 1: Overview ====================

function OverviewTab({
  persona,
  onEdit,
}: {
  persona: PersonaInfo;
  onEdit: () => void;
}) {
  const { token } = theme.useToken();
  const { data: metrics, isLoading: metricsLoading } = usePersonaMetrics(persona.id);

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <GlowCard
        title="身份信息"
        extra={
          <Button size="small" icon={<EditOutlined />} onClick={onEdit}>
            编辑
          </Button>
        }
      >
        <Descriptions column={2} size="small">
          <Descriptions.Item label="ID">
            <Tag>{persona.id}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="名称">{persona.name}</Descriptions.Item>
          <Descriptions.Item label="部门">
            <Tag color="blue">{persona.department_name ?? persona.department}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="LLM 配置">
            {persona.llm_config_name ? (
              <Tag color="orange">{persona.llm_config_name}</Tag>
            ) : (
              <Text type="secondary">全局配置</Text>
            )}
          </Descriptions.Item>
        </Descriptions>
      </GlowCard>

      <GlowCard title="工具与技能">
        <div style={{ marginBottom: 12 }}>
          <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
            工具权限等级
          </Text>
          <div style={{ marginTop: 4 }}>
            <Tag
              color={
                persona.tool_tier_max >= 2
                  ? "green"
                  : persona.tool_tier_max >= 1
                    ? "blue"
                    : "default"
              }
            >
              Tier {persona.tool_tier_max}
            </Tag>
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 4 }}>
              {persona.tool_tier_max === 0
                ? "仅监控/审核，不操作工具"
                : persona.tool_tier_max === 1
                  ? "基础工具（只读）"
                  : `可使用 tier <= ${persona.tool_tier_max} 的所有工具`}
            </Text>
          </div>
        </div>

        <Row gutter={16}>
          <Col span={8}>
            <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>允许工具</Text>
            <div style={{ marginTop: 4 }}>
              {persona.tools_allowed.length > 0 ? (
                persona.tools_allowed.map((t) => (
                  <Tag key={t} style={{ marginBottom: 4 }}>{t}</Tag>
                ))
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>无指定（按 tier 过滤）</Text>
              )}
            </div>
          </Col>
          <Col span={8}>
            <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>禁用工具</Text>
            <div style={{ marginTop: 4 }}>
              {persona.tools_denied.length > 0 ? (
                persona.tools_denied.map((t) => (
                  <Tag key={t} color="red" style={{ marginBottom: 4 }}>{t}</Tag>
                ))
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>无</Text>
              )}
            </div>
          </Col>
          <Col span={8}>
            <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>技能注入</Text>
            <div style={{ marginTop: 4 }}>
              {persona.skills_allowed.length > 0 ? (
                persona.skills_allowed.map((s) => (
                  <Tag key={s} color="purple" style={{ marginBottom: 4 }}>{s}</Tag>
                ))
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>全部技能（无过滤）</Text>
              )}
            </div>
          </Col>
        </Row>
      </GlowCard>

      <GlowCard title="委派关系">
        <Descriptions column={2} size="small">
          <Descriptions.Item label="可委派">
            {persona.can_delegate ? <Tag color="green">是</Tag> : <Tag>否</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="可委派至">
            {persona.delegates_to.length > 0 ? (
              persona.delegates_to.map((d) => (
                <Tag key={d} color="cyan" style={{ marginBottom: 4 }}>{d}</Tag>
              ))
            ) : (
              <Text type="secondary">无</Text>
            )}
          </Descriptions.Item>
        </Descriptions>
      </GlowCard>

      <GlowCard title="执行指标">
        {metricsLoading ? (
          <div style={{ textAlign: "center", padding: 16 }}>
            <Spin size="small" />
          </div>
        ) : metrics ? (
          <Space direction="vertical" size="small" style={{ width: "100%" }}>
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title="总执行" value={metrics.total_executions} />
              </Col>
              <Col span={6}>
                <Statistic
                  title="完成"
                  value={metrics.completed}
                  valueStyle={{ color: token.colorSuccess }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="失败"
                  value={metrics.failed}
                  valueStyle={{ color: token.colorError }}
                />
              </Col>
              <Col span={6}>
                <div>
                  <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
                    成功率
                  </Text>
                  <Progress
                    percent={Number(metrics.success_rate.toFixed(1))}
                    size="small"
                    status={metrics.success_rate >= 80 ? "success" : "normal"}
                  />
                </div>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title="总 Token" value={metrics.total_tokens} />
              </Col>
              <Col span={6}>
                <Statistic title="均 Token" value={metrics.avg_tokens_per_execution} />
              </Col>
              <Col span={6}>
                <Statistic
                  title="总成本"
                  value={metrics.total_cost_cny}
                  prefix="¥"
                  precision={4}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="均耗时"
                  value={metrics.avg_duration_seconds}
                  suffix="s"
                  precision={1}
                />
              </Col>
            </Row>
          </Space>
        ) : (
          <Text type="secondary">暂无指标数据</Text>
        )}
      </GlowCard>
    </Space>
  );
}

// ==================== Tab 2: Prompt Files ====================

function PromptFilesTab({ personaId }: { personaId: string }) {
  const { token } = theme.useToken();
  const { data: promptData } = usePromptFiles();
  const promptFiles = promptData?.files ?? [];
  const [editingFile, setEditingFile] = useState<{
    personaId: string;
    filename: string;
  } | null>(null);
  const [editContent, setEditContent] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);

  const { data: fileContent, isLoading: contentLoading } = usePromptFileContent(
    editingFile?.personaId ?? null,
    editingFile?.filename ?? null,
  );
  const updateMutation = useUpdatePromptFile();
  const { data: previewData, isLoading: previewLoading } = usePromptPreview(
    previewOpen ? personaId : null,
  );
  const { data: layers, isLoading: layersLoading } = usePromptLayers(personaId);

  const personaFiles = promptFiles.filter(
    (f: PromptFileInfo) => f.persona_id === personaId,
  );

  const handleEdit = (pid: string, filename: string) => {
    setEditingFile({ personaId: pid, filename });
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

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <GlowCard
        title="指令文件"
        extra={
          <Button icon={<EyeOutlined />} onClick={() => setPreviewOpen(true)}>
            预览完整 Prompt
          </Button>
        }
      >
        {personaFiles.length === 0 ? (
          <Text type="secondary">暂无 Prompt 文件</Text>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
            {personaFiles.map((f: PromptFileInfo) => (
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
                  <Text strong>{f.filename}</Text>
                  <Button
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => handleEdit(f.persona_id, f.filename)}
                  />
                </div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {f.size} bytes
                </Text>
                {f.modified && (
                  <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                    {new Date(f.modified).toLocaleString("zh-CN")}
                  </Text>
                )}
              </div>
            ))}
          </div>
        )}
      </GlowCard>

      {layers && (
        <Card title="Prompt 分层分析" size="small" loading={layersLoading}>
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
              {
                title: "来源",
                dataIndex: "source",
                key: "source",
                ellipsis: true,
                render: (v: string) => <Text style={{ fontSize: 12 }}>{v}</Text>,
              },
              { title: "字符", dataIndex: "chars", key: "chars", width: 80, align: "right" as const },
              { title: "Token (est)", dataIndex: "tokens_est", key: "tokens_est", width: 100, align: "right" as const },
              {
                title: "占比",
                key: "percent",
                width: 120,
                render: (_: unknown, record: { chars: number }) => (
                  <Progress
                    percent={Math.round((record.chars / (layers.total_chars || 1)) * 100)}
                    size="small"
                    strokeColor={record.chars > 5000 ? "#faad14" : "#1890ff"}
                  />
                ),
              },
              {
                title: "操作",
                key: "actions",
                width: 70,
                align: "center" as const,
                render: (_: unknown, record: { name: string }) => {
                  const editableMap: Record<string, { pid: string; filename: string }> = {
                    "COURT.md": { pid: "court", filename: "COURT.md" },
                    "Court MEMORY.md": { pid: "court", filename: "MEMORY.md" },
                    "SOUL.md": { pid: personaId, filename: "SOUL.md" },
                    "ROLE.md": { pid: personaId, filename: "ROLE.md" },
                    "MEMORY.md": { pid: personaId, filename: "MEMORY.md" },
                  };
                  const target = editableMap[record.name];
                  if (!target) return null;
                  return (
                    <Button
                      type="text"
                      size="small"
                      icon={<EditOutlined />}
                      onClick={() => handleEdit(target.pid, target.filename)}
                    />
                  );
                },
              },
            ]}
            dataSource={layers.layers.map((l) => ({ key: l.layer, ...l }))}
            size="small"
            pagination={false}
          />
        </Card>
      )}

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

      <Modal
        title={`System Prompt 预览: ${personaId}`}
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
          <Text type="secondary">无法生成预览</Text>
        )}
      </Modal>
    </Space>
  );
}

// ==================== Tab 3: Execution History ====================

function MemorialBubble({ memorial }: { memorial: MemorialBrief }) {
  const { token } = theme.useToken();
  const isFailed = memorial.status === "failed";

  return (
    <div style={{ marginBottom: 16 }}>
      {memorial.instruction && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
          <div
            style={{
              maxWidth: "80%",
              padding: "8px 12px",
              borderRadius: 12,
              borderTopRightRadius: 2,
              background: token.colorPrimaryBg,
              border: `1px solid ${token.colorPrimaryBorder}`,
            }}
          >
            <Text style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
              {memorial.instruction}
            </Text>
          </div>
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-start" }}>
        <div
          style={{
            maxWidth: "80%",
            padding: "8px 12px",
            borderRadius: 12,
            borderTopLeftRadius: 2,
            background: isFailed ? token.colorErrorBg : token.colorBgContainer,
            border: `1px solid ${isFailed ? token.colorErrorBorder : token.colorBorder}`,
          }}
        >
          {isFailed && memorial.error ? (
            <Text type="danger" style={{ fontSize: 13 }}>{memorial.error}</Text>
          ) : memorial.result ? (
            <Paragraph
              ellipsis={{ rows: 6, expandable: true, symbol: "展开" }}
              style={{ marginBottom: 0, fontSize: 13, whiteSpace: "pre-wrap" }}
            >
              {memorial.result}
            </Paragraph>
          ) : memorial.summary ? (
            <Text type="secondary" style={{ fontSize: 13 }}>{memorial.summary}</Text>
          ) : (
            <Tag color={statusColors[memorial.status] ?? "default"}>
              {memorial.status}
            </Tag>
          )}
          <div style={{ marginTop: 4, textAlign: "right" }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              {new Date(memorial.created_at).toLocaleString("zh-CN")}
            </Text>
          </div>
        </div>
      </div>
    </div>
  );
}

function ExecutionHistoryTab({ personaId }: { personaId: string }) {
  const navigate = useNavigate();
  const { data: groups, isLoading } = usePersonaMemorials(personaId);

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!groups || groups.length === 0) {
    return <Empty description="暂无执行记录" />;
  }

  return (
    <Collapse
      accordion
      items={groups.map((group: EdictMemorialGroup) => ({
        key: group.edict_id,
        label: (
          <Space>
            <Text strong>{group.edict_title || group.edict_goal}</Text>
            <Tag color={statusColors[group.edict_status] ?? "blue"}>
              {group.edict_status}
            </Tag>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {group.memorials.length} 条奏折
            </Text>
          </Space>
        ),
        extra: (
          <Button
            type="link"
            size="small"
            onClick={(e) => {
              e.stopPropagation();
              navigate(`/edicts/${group.edict_id}`);
            }}
          >
            查看敕令
          </Button>
        ),
        children: (
          <div style={{ padding: "8px 0" }}>
            {group.memorials.map((m: MemorialBrief) => (
              <MemorialBubble key={m.id} memorial={m} />
            ))}
          </div>
        ),
      }))}
    />
  );
}

// ==================== Tab 4: Memory ====================

function MemoryTab({ personaId }: { personaId: string }) {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MemoryEntry[] | null>(null);

  const { data: memories, isLoading } = usePersonaMemory(personaId);
  const deleteMutation = useDeleteMemory();
  const recallMutation = useRecallMemory();

  const handleSearch = () => {
    if (!searchQuery.trim()) {
      setSearchResults(null);
      return;
    }
    recallMutation.mutate(
      { persona_id: personaId, query: searchQuery, include_shared: true, limit: 30 },
      { onSuccess: (data) => setSearchResults(data) },
    );
  };

  const handleDelete = (entryId: string) => {
    deleteMutation.mutate(entryId, {
      onSuccess: () => notification.success({ message: "记忆已删除" }),
    });
  };

  const displayData = searchResults ?? memories ?? [];

  const columns: ColumnsType<MemoryEntry> = [
    {
      title: "分类",
      dataIndex: "category",
      key: "category",
      width: 110,
      render: (v: string) => <Tag color={categoryColors[v] ?? "default"}>{v}</Tag>,
      filters: [
        { text: "观察", value: "observation" },
        { text: "洞察", value: "insight" },
        { text: "实体", value: "entity" },
        { text: "摘要", value: "summary" },
      ],
      onFilter: (value, record) => record.category === value,
    },
    { title: "内容", dataIndex: "content", key: "content", ellipsis: true },
    {
      title: "来源",
      dataIndex: "source",
      key: "source",
      width: 100,
      render: (v: string) => <Tag color={sourceColors[v] ?? "default"}>{v}</Tag>,
    },
    {
      title: "置信度",
      dataIndex: "confidence",
      key: "confidence",
      width: 90,
      align: "right",
      render: (v: number) => `${(v * 100).toFixed(0)}%`,
    },
    {
      title: "时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      render: (v: string) => new Date(v).toLocaleString("zh-CN"),
    },
    {
      title: "",
      key: "actions",
      width: 50,
      render: (_, record) => (
        <Popconfirm title="确定删除此记忆？" onConfirm={() => handleDelete(record.id)}>
          <Button type="text" danger size="small" icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card size="small">
        <Space.Compact style={{ width: "100%" }}>
          <Input
            placeholder="搜索记忆..."
            prefix={<SearchOutlined />}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onPressEnter={handleSearch}
            allowClear
            onClear={() => setSearchResults(null)}
          />
          <Button type="primary" loading={recallMutation.isPending} onClick={handleSearch}>
            检索
          </Button>
        </Space.Compact>
        {searchResults && (
          <Text type="secondary" style={{ marginTop: 8, display: "block" }}>
            找到 {searchResults.length} 条匹配 &quot;{searchQuery}&quot; 的记忆
          </Text>
        )}
      </Card>

      <Card
        title="记忆条目"
        extra={<Text type="secondary">{displayData.length} 条</Text>}
      >
        {displayData.length === 0 && !isLoading ? (
          <Empty description="暂无记忆" />
        ) : (
          <Table<MemoryEntry>
            columns={columns}
            dataSource={displayData}
            rowKey="id"
            loading={isLoading}
            size="small"
            pagination={{ pageSize: 15, showSizeChanger: true }}
          />
        )}
      </Card>
    </Space>
  );
}

// ==================== Main Page ====================

export default function PersonaDetailPage() {
  const { personaId } = useParams<{ personaId: string }>();
  const navigate = useNavigate();

  const { data: personas, isLoading } = usePersonas();
  const { data: departments } = useDepartments();
  const { data: tools } = useTools();
  const { data: skills } = useSkills();
  const { data: configsData } = useConfigs();

  const persona = useMemo(
    () => (personas ?? []).find((p) => p.id === personaId) ?? null,
    [personas, personaId],
  );

  const [editOpen, setEditOpen] = useState(false);
  const [form] = Form.useForm();
  const updateMutation = useUpdatePersona();

  const openEdit = () => {
    if (!persona) return;
    form.setFieldsValue({
      name: persona.name,
      department: persona.department,
      tools_allowed: persona.tools_allowed,
      tools_denied: persona.tools_denied,
      skills_allowed: persona.skills_allowed,
      tool_tier_max: persona.tool_tier_max,
      can_delegate: persona.can_delegate,
      delegates_to: persona.delegates_to,
      llm_config_name: persona.llm_config_name ?? "",
    });
    setEditOpen(true);
  };

  const handleSave = (values: PersonaUpdateRequest) => {
    if (!personaId) return;
    const body = { ...values, llm_config_name: values.llm_config_name || null };
    updateMutation.mutate(
      { id: personaId, body },
      {
        onSuccess: () => {
          notification.success({ message: "官员已更新" });
          setEditOpen(false);
        },
      },
    );
  };

  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "50vh" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!persona) {
    return (
      <PageContainer title="官员详情">
        <Text type="secondary">未找到该官员</Text>
      </PageContainer>
    );
  }

  const llmConfigOptions = [
    { value: "", label: "使用全局配置（默认）" },
    ...(configsData?.configs ?? []).map((c) => ({
      value: c.name,
      label: `${c.name} (${c.model})`,
    })),
  ];
  const toolOptions = (tools ?? []).map((t) => ({
    value: t.name,
    label: `${t.name} (tier ${t.tier})`,
  }));
  const skillOptions = (skills ?? []).map((s) => ({
    value: s.name,
    label: `${s.name}${s.description ? ` — ${s.description}` : ""}`,
  }));
  const deptOptions = (departments ?? []).map((d) => ({
    value: d.id,
    label: `${d.name} (${d.id})`,
  }));

  return (
    <PageContainer
      title={`${persona.name}: ${persona.department_name ?? persona.department}`}
      extra={
        <Space>
          <Button icon={<EditOutlined />} onClick={openEdit}>
            编辑
          </Button>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/personas")}>
            返回百官阁
          </Button>
        </Space>
      }
    >
      <Tabs
        defaultActiveKey="overview"
        items={[
          {
            key: "overview",
            label: (
              <Space>
                <UserOutlined />
                概览
              </Space>
            ),
            children: <OverviewTab persona={persona} onEdit={openEdit} />,
          },
          {
            key: "prompt",
            label: (
              <Space>
                <FileTextOutlined />
                指令文件
              </Space>
            ),
            children: <PromptFilesTab personaId={persona.id} />,
          },
          {
            key: "history",
            label: (
              <Space>
                <HistoryOutlined />
                执行记录
              </Space>
            ),
            children: <ExecutionHistoryTab personaId={persona.id} />,
          },
          {
            key: "memory",
            label: (
              <Space>
                <SearchOutlined />
                记忆
              </Space>
            ),
            children: <MemoryTab personaId={persona.id} />,
          },
          {
            key: "profile",
            label: (
              <Space>
                <TrophyOutlined />
                成长档案
              </Space>
            ),
            children: <ProfileTab personaId={persona.id} />,
          },
        ]}
      />

      <Modal
        title="编辑官员"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={updateMutation.isPending}
        destroyOnClose
        width={560}
      >
        <Form form={form} layout="vertical" onFinish={handleSave}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="department" label="部门" rules={[{ required: true, message: "请选择部门" }]}>
            <Select options={deptOptions} showSearch optionFilterProp="label" />
          </Form.Item>
          <Form.Item name="llm_config_name" label="LLM 配置">
            <Select options={llmConfigOptions} allowClear />
          </Form.Item>
          <Form.Item name="tools_allowed" label="允许工具">
            <Select mode="multiple" options={toolOptions} showSearch optionFilterProp="label" placeholder="选择允许使用的工具" />
          </Form.Item>
          <Form.Item name="tools_denied" label="禁用工具">
            <Select mode="multiple" options={toolOptions} showSearch optionFilterProp="label" placeholder="选择禁用的工具" />
          </Form.Item>
          <Form.Item name="skills_allowed" label="技能">
            <Select mode="multiple" options={skillOptions} showSearch optionFilterProp="label" placeholder="选择技能（留空 = 全部注入）" />
          </Form.Item>
          <Form.Item name="tool_tier_max" label="最大工具等级">
            <InputNumber min={0} max={10} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="can_delegate" label="可委派" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="delegates_to" label="可委派目标">
            <Select
              mode="multiple"
              placeholder="选择可委派的官员"
              options={(personas ?? [])
                .filter((p) => p.id !== personaId)
                .map((p) => ({ value: p.id, label: `${p.name} (${p.id})` }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}
