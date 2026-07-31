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
  ReloadOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import PageQueryError from "../components/states/PageQueryError";
import {
  usePersonas,
  useRegeneratePersonaIdentity,
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
import { useMCPServers } from "../hooks/useMCP";
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
import { useT } from "../i18n";

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
  const t = useT();
  const { token } = theme.useToken();
  const { data: metrics, isLoading: metricsLoading } = usePersonaMetrics(persona.id);

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <GlowCard
        title={t("persona.detail.section.identity")}
        extra={
          <Button size="small" icon={<EditOutlined />} onClick={onEdit}>
            {t("action.edit")}
          </Button>
        }
      >
        <Descriptions column={2} size="small">
          <Descriptions.Item label={t("persona.detail.field.id")}>
            <Tag>{persona.id}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label={t("persona.detail.field.name")}>{persona.name}</Descriptions.Item>
          <Descriptions.Item label={t("persona.detail.field.dept")}>
            <Tag color="blue">{persona.department_name ?? persona.department}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label={t("persona.detail.field.title")}>
            {persona.title ? (
              <Tag color="purple">{persona.title}</Tag>
            ) : (
              <Text type="secondary">{t("persona.detail.value.notAssigned")}</Text>
            )}
          </Descriptions.Item>
          <Descriptions.Item label={t("persona.detail.field.llmConfig")}>
            {persona.llm_config_name ? (
              <Tag color="orange">{persona.llm_config_name}</Tag>
            ) : (
              <Text type="secondary">{t("persona.detail.value.globalConfig")}</Text>
            )}
          </Descriptions.Item>
        </Descriptions>
      </GlowCard>

      <GlowCard title={t("persona.detail.section.toolsSkills")}>
        <div style={{ marginBottom: 12 }}>
          <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
            {t("persona.detail.field.toolPermission")}
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
                ? t("persona.tier.tier0")
                : persona.tool_tier_max === 1
                  ? t("persona.tier.tier1")
                  : t("persona.tier.tierN", { n: persona.tool_tier_max })}
            </Text>
          </div>
        </div>

        <Row gutter={16}>
          <Col span={8}>
            <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>{t("persona.detail.field.toolsAllowed")}</Text>
            <div style={{ marginTop: 4 }}>
              {persona.tools_allowed.length > 0 ? (
                persona.tools_allowed.map((tool) => (
                  <Tag key={tool} style={{ marginBottom: 4 }}>{tool}</Tag>
                ))
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>{t("persona.detail.value.noSpecified")}</Text>
              )}
            </div>
          </Col>
          <Col span={8}>
            <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>{t("persona.detail.field.toolsDenied")}</Text>
            <div style={{ marginTop: 4 }}>
              {persona.tools_denied.length > 0 ? (
                persona.tools_denied.map((tool) => (
                  <Tag key={tool} color="red" style={{ marginBottom: 4 }}>{tool}</Tag>
                ))
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>{t("persona.detail.value.none")}</Text>
              )}
            </div>
          </Col>
          <Col span={8}>
            <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>{t("persona.detail.field.skills")}</Text>
            <div style={{ marginTop: 4 }}>
              {persona.skills_allowed.length > 0 ? (
                persona.skills_allowed.map((s) => (
                  <Tag key={s} color="purple" style={{ marginBottom: 4 }}>{s}</Tag>
                ))
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>{t("persona.detail.value.allSkills")}</Text>
              )}
            </div>
          </Col>
        </Row>
      </GlowCard>

      <GlowCard title={t("persona.detail.section.delegation")}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label={t("persona.detail.field.canDelegate")}>
            {persona.can_delegate ? <Tag color="green">{t("persona.detail.value.yes")}</Tag> : <Tag>{t("persona.detail.value.no")}</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label={t("persona.detail.field.memoryGlobalRead")}>
            {persona.memory_global_read ? <Tag color="orange">{t("persona.detail.value.yes")}</Tag> : <Tag>{t("persona.detail.value.no")}</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label={t("persona.detail.field.delegatesTo")}>
            {persona.delegates_to.length > 0 ? (
              persona.delegates_to.map((d) => (
                <Tag key={d} color="cyan" style={{ marginBottom: 4 }}>{d}</Tag>
              ))
            ) : (
              <Text type="secondary">{t("persona.detail.value.none")}</Text>
            )}
          </Descriptions.Item>
        </Descriptions>
      </GlowCard>

      <GlowCard title={t("persona.detail.section.metrics")}>
        {metricsLoading ? (
          <div style={{ textAlign: "center", padding: 16 }}>
            <Spin size="small" />
          </div>
        ) : metrics ? (
          <Space direction="vertical" size="small" style={{ width: "100%" }}>
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title={t("persona.metric.total")} value={metrics.total_executions} />
              </Col>
              <Col span={6}>
                <Statistic
                  title={t("persona.metric.completed")}
                  value={metrics.completed}
                  valueStyle={{ color: token.colorSuccess }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title={t("persona.metric.failed")}
                  value={metrics.failed}
                  valueStyle={{ color: token.colorError }}
                />
              </Col>
              <Col span={6}>
                <div>
                  <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
                    {t("persona.metric.successRate")}
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
                <Statistic title={t("persona.metric.totalTokens")} value={metrics.total_tokens} />
              </Col>
              <Col span={6}>
                <Statistic title={t("persona.metric.avgTokens")} value={metrics.avg_tokens_per_execution} />
              </Col>
              <Col span={6}>
                <Statistic
                  title={t("persona.metric.totalCost")}
                  value={metrics.total_cost_cny}
                  prefix="¥"
                  precision={4}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title={t("persona.metric.avgDuration")}
                  value={metrics.avg_duration_seconds}
                  suffix="s"
                  precision={1}
                />
              </Col>
            </Row>
          </Space>
        ) : (
          <Text type="secondary">{t("persona.metric.empty")}</Text>
        )}
      </GlowCard>
    </Space>
  );
}

// ==================== Tab 2: Prompt Files ====================

function PromptFilesTab({ personaId }: { personaId: string }) {
  const t = useT();
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
          notification.success({ message: t("system.toast.fileSaved") });
          setEditingFile(null);
        },
      },
    );
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <GlowCard
        title={t("persona.detail.section.instructionFiles")}
        extra={
          <Button icon={<EyeOutlined />} onClick={() => setPreviewOpen(true)}>
            {t("persona.detail.previewBtn")}
          </Button>
        }
      >
        {personaFiles.length === 0 ? (
          <Text type="secondary">{t("system.prompt.emptyFiles")}</Text>
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
        <Card title={t("system.prompt.layeredAnalysis")} size="small" loading={layersLoading}>
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
              {
                title: t("system.prompt.table.source"),
                dataIndex: "source",
                key: "source",
                ellipsis: true,
                render: (v: string) => <Text style={{ fontSize: 12 }}>{v}</Text>,
              },
              { title: t("system.prompt.table.chars"), dataIndex: "chars", key: "chars", width: 80, align: "right" as const },
              { title: t("system.prompt.table.tokensEst"), dataIndex: "tokens_est", key: "tokens_est", width: 100, align: "right" as const },
              {
                title: t("system.prompt.table.percent"),
                key: "percent",
                width: 120,
                render: (_: unknown, record: { chars: number; name: string }) => (
                  <Progress
                    percent={Math.round((record.chars / (layers.total_chars || 1)) * 100)}
                    size="small"
                    strokeColor={record.chars > 5000 ? "var(--ts-color-warning)" : "var(--ts-color-info)"}
                  />
                ),
              },
              {
                title: t("system.prompt.table.actions"),
                key: "actions",
                width: 70,
                align: "center" as const,
                render: (_: unknown, record: { chars: number; name: string }) => {
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

      <Modal
        title={t("system.prompt.previewModalTitle", { persona: personaId })}
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
          <Text type="secondary">{t("system.prompt.previewEmpty")}</Text>
        )}
      </Modal>
    </Space>
  );
}

// ==================== Tab 3: Execution History ====================

function MemorialBubble({ memorial }: { memorial: MemorialBrief }) {
  const t = useT();
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
              ellipsis={{ rows: 6, expandable: true, symbol: t("memory.history.expand") }}
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
  const t = useT();
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
    return <Empty description={t("persona.detail.history.empty")} />;
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
              {t("persona.detail.history.count", { n: group.memorials.length })}
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
            {t("persona.detail.history.viewEdict")}
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
  const t = useT();
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
      onSuccess: () => notification.success({ message: t("memory.toast.deleted") }),
    });
  };

  const displayData = searchResults ?? memories ?? [];

  const columns: ColumnsType<MemoryEntry> = [
    {
      title: t("memory.table.category"),
      dataIndex: "category",
      key: "category",
      width: 110,
      render: (v: string) => <Tag color={categoryColors[v] ?? "default"}>{v}</Tag>,
      filters: [
        { text: t("memory.category.observation"), value: "observation" },
        { text: t("memory.category.insight"), value: "insight" },
        { text: t("memory.category.entity"), value: "entity" },
        { text: t("memory.category.summary"), value: "summary" },
      ],
      onFilter: (value, record) => record.category === value,
    },
    { title: t("memory.table.content"), dataIndex: "content", key: "content", ellipsis: true },
    {
      title: t("memory.table.source"),
      dataIndex: "source",
      key: "source",
      width: 100,
      render: (v: string) => <Tag color={sourceColors[v] ?? "default"}>{v}</Tag>,
    },
    {
      title: t("memory.table.confidence"),
      dataIndex: "confidence",
      key: "confidence",
      width: 90,
      align: "right",
      render: (v: number) => `${(v * 100).toFixed(0)}%`,
    },
    {
      title: t("memory.table.time"),
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
        <Popconfirm title={t("memory.selection.confirmDelete")} onConfirm={() => handleDelete(record.id)}>
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
            placeholder={t("memory.search.placeholder")}
            prefix={<SearchOutlined />}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onPressEnter={handleSearch}
            allowClear
            onClear={() => setSearchResults(null)}
          />
          <Button type="primary" loading={recallMutation.isPending} onClick={handleSearch}>
            {t("memory.search.submit")}
          </Button>
        </Space.Compact>
        {searchResults && (
          <Text type="secondary" style={{ marginTop: 8, display: "block" }}>
            {t("memory.search.summary", { n: searchResults.length, q: searchQuery })}
          </Text>
        )}
      </Card>

      <Card
        title={t("persona.detail.section.memoryEntries")}
        extra={<Text type="secondary">{t("persona.detail.memory.count", { n: displayData.length })}</Text>}
      >
        {displayData.length === 0 && !isLoading ? (
          <Empty description={t("memory.empty")} />
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
  const t = useT();
  const { personaId } = useParams<{ personaId: string }>();
  const navigate = useNavigate();

  const personasQuery = usePersonas();
  const { data: personas, isLoading } = personasQuery;
  const { data: departments } = useDepartments();
  const { data: tools } = useTools();
  const { data: skills } = useSkills();
  const { data: configsData } = useConfigs();
  const { data: mcpServers } = useMCPServers();

  const persona = useMemo(
    () => (personas ?? []).find((p) => p.id === personaId) ?? null,
    [personas, personaId],
  );

  const [editOpen, setEditOpen] = useState(false);
  const [form] = Form.useForm();
  const updateMutation = useUpdatePersona();
  const regenerateMutation = useRegeneratePersonaIdentity();

  if (personasQuery.error) {
    return (
      <PageContainer title={t("persona.detail.title")}>
        <PageQueryError
          error={personasQuery.error}
          onRetry={() => void personasQuery.refetch()}
        />
      </PageContainer>
    );
  }

  const openEdit = () => {
    if (!persona) return;
    form.setFieldsValue({
      name: persona.name,
      department: persona.department,
      title: persona.title ?? "",
      tools_allowed: persona.tools_allowed,
      tools_denied: persona.tools_denied,
      skills_allowed: persona.skills_allowed,
      tool_tier_max: persona.tool_tier_max,
      can_delegate: persona.can_delegate,
      memory_global_read: persona.memory_global_read,
      delegates_to: persona.delegates_to,
      llm_config_name: persona.llm_config_name ?? "",
    });
    setEditOpen(true);
  };

  const handleRegenerate = () => {
    if (!personaId) return;
    regenerateMutation.mutate(personaId, {
      onSuccess: () => {
        notification.success({
          message: t("persona.toast.regenerateSuccess"),
          description: t("persona.toast.regenerateSuccessDesc"),
        });
      },
      onError: (err: Error) => {
        notification.error({ message: err.message ?? t("persona.toast.regenerateFailed") });
      },
    });
  };

  const handleSave = (values: PersonaUpdateRequest) => {
    if (!personaId) return;
    const body = { ...values, llm_config_name: values.llm_config_name || null };
    updateMutation.mutate(
      { id: personaId, body },
      {
        onSuccess: () => {
          notification.success({ message: t("persona.toast.personaUpdated") });
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
      <PageContainer title={t("persona.detail.title")}>
        <Text type="secondary">{t("persona.detail.notFound")}</Text>
      </PageContainer>
    );
  }

  const llmConfigOptions = [
    { value: "", label: t("persona.form.persona.llmConfigGlobal") },
    ...(configsData?.configs ?? []).map((c) => ({
      value: c.name,
      label: `${c.name} (${c.model})`,
    })),
  ];
  // MCP server 整体授权快捷项：mcp_<server>_*（fnmatch 通配符，后端 P4 支持）
  const mcpWildcardOptions = (mcpServers ?? []).map((s) => ({
    value: `mcp_${s.name}_*`,
    label: `${t("persona.form.persona.mcpAllOf", { name: s.name })} — mcp_${s.name}_*`,
  }));
  const toolOptions = [
    ...mcpWildcardOptions,
    ...(tools ?? []).map((tool) => ({
      value: tool.name,
      label: `${tool.name} (tier ${tool.tier})`,
    })),
  ];
  const skillOptions = (skills ?? []).map((s) => ({
    value: s.name,
    label: `${s.name}${s.description ? ` — ${s.description}` : ""}`,
  }));
  const deptOptions = (departments ?? []).map((d) => ({
    value: d.id,
    label: `${d.name} (${d.id})`,
  }));

  const dept = persona.department_name ?? persona.department;
  const pageTitle = persona.title
    ? t("persona.detail.pageTitleWithTitle", { name: persona.name, title: persona.title, dept })
    : t("persona.detail.pageTitle", { name: persona.name, dept });

  return (
    <PageContainer
      title={pageTitle}
      extra={
        <Space>
          <Button icon={<EditOutlined />} onClick={openEdit}>
            {t("action.edit")}
          </Button>
          <Popconfirm
            title={t("persona.detail.regenerateConfirm")}
            description={t("persona.detail.regenerateDesc")}
            onConfirm={handleRegenerate}
            okText={t("persona.detail.regenerateOk")}
            cancelText={t("common.cancel")}
          >
            <Button
              icon={<ReloadOutlined />}
              loading={regenerateMutation.isPending}
            >
              {t("persona.detail.regenerate")}
            </Button>
          </Popconfirm>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/personas")}>
            {t("persona.detail.back", { name: t("persona.title") })}
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
                {t("persona.detail.tab.overview")}
              </Space>
            ),
            children: <OverviewTab persona={persona} onEdit={openEdit} />,
          },
          {
            key: "prompt",
            label: (
              <Space>
                <FileTextOutlined />
                {t("persona.detail.tab.prompt")}
              </Space>
            ),
            children: <PromptFilesTab personaId={persona.id} />,
          },
          {
            key: "history",
            label: (
              <Space>
                <HistoryOutlined />
                {t("persona.detail.tab.history")}
              </Space>
            ),
            children: <ExecutionHistoryTab personaId={persona.id} />,
          },
          {
            key: "memory",
            label: (
              <Space>
                <SearchOutlined />
                {t("persona.detail.tab.memory")}
              </Space>
            ),
            children: <MemoryTab personaId={persona.id} />,
          },
          {
            key: "profile",
            label: (
              <Space>
                <TrophyOutlined />
                {t("persona.detail.tab.profile")}
              </Space>
            ),
            children: <ProfileTab personaId={persona.id} />,
          },
        ]}
      />

      <Modal
        title={t("persona.form.persona.editTitle")}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={updateMutation.isPending}
        destroyOnClose
        width={560}
      >
        <Form form={form} layout="vertical" onFinish={handleSave}>
          <Form.Item name="name" label={t("persona.form.persona.field.name")} rules={[{ required: true, message: t("persona.form.persona.validation.nameRequired") }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="title"
            label={t("persona.form.persona.field.title")}
            rules={[{ max: 32, message: t("persona.form.persona.validation.titleMax") }]}
            tooltip={t("persona.form.persona.tooltip.title")}
          >
            <Input placeholder={t("persona.form.persona.placeholder.title")} maxLength={32} allowClear />
          </Form.Item>
          <Form.Item name="department" label={t("persona.form.persona.field.department")} rules={[{ required: true, message: t("persona.form.persona.validation.departmentRequired") }]}>
            <Select options={deptOptions} showSearch optionFilterProp="label" />
          </Form.Item>
          <Form.Item name="llm_config_name" label={t("persona.form.persona.field.llmConfig")}>
            <Select options={llmConfigOptions} allowClear />
          </Form.Item>
          <Form.Item
            name="tools_allowed"
            label={t("persona.form.persona.field.toolsAllowed")}
            extra={t("persona.form.persona.toolsWildcardHint")}
          >
            <Select
              mode="tags"
              options={toolOptions}
              showSearch
              optionFilterProp="label"
              placeholder={t("persona.form.persona.placeholder.toolsAllowed")}
              tokenSeparators={[",", " "]}
            />
          </Form.Item>
          <Form.Item
            name="tools_denied"
            label={t("persona.form.persona.field.toolsDenied")}
            extra={t("persona.form.persona.toolsWildcardHint")}
          >
            <Select
              mode="tags"
              options={toolOptions}
              showSearch
              optionFilterProp="label"
              placeholder={t("persona.form.persona.placeholder.toolsDenied")}
              tokenSeparators={[",", " "]}
            />
          </Form.Item>
          <Form.Item name="skills_allowed" label={t("persona.form.persona.field.skills")}>
            <Select mode="multiple" options={skillOptions} showSearch optionFilterProp="label" placeholder={t("persona.form.persona.placeholder.skills")} />
          </Form.Item>
          <Form.Item name="tool_tier_max" label={t("persona.form.persona.field.tierMax")}>
            <InputNumber min={0} max={10} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="can_delegate" label={t("persona.form.persona.field.canDelegate")} valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="memory_global_read" label={t("persona.form.persona.field.memoryGlobalRead")} valuePropName="checked" extra={t("persona.form.persona.fieldHint.memoryGlobalRead")}>
            <Switch />
          </Form.Item>
          <Form.Item name="delegates_to" label={t("persona.form.persona.field.delegatesTo")}>
            <Select
              mode="multiple"
              placeholder={t("persona.form.persona.placeholder.delegatesTo")}
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
