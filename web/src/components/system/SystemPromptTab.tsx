import { useState } from "react";
import {
  Segmented,
  Typography,
  Drawer,
  Input,
  Spin,
  Modal,
  Button,
  Card,
  Row,
  Col,
  Statistic,
  Table,
  Progress,
  Tag,
  theme,
  notification,
} from "antd";
import { EditOutlined, EyeOutlined } from "@ant-design/icons";
import { usePromptLayers } from "../../hooks/useOps";
import { usePersonas } from "../../hooks/usePersonas";
import {
  usePromptFiles,
  usePromptFileContent,
  useUpdatePromptFile,
  usePromptPreview,
} from "../../hooks/useSystem";
import { useT } from "../../i18n";
import { monoStyle } from "./shared";
import PageQueryError from "../states/PageQueryError";

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
  const layersQuery = usePromptLayers(personaId);
  const { data: layers, isLoading } = layersQuery;

  if (!personaId) return null;
  if (layersQuery.error) {
    return (
      <div style={{ marginTop: 16 }}>
        <PageQueryError
          error={layersQuery.error}
          onRetry={() => void layersQuery.refetch()}
        />
      </div>
    );
  }
  if (!layers) {
    return isLoading ? (
      <Card
        title={title ?? t("system.prompt.layeredAnalysis")}
        size="small"
        loading
        style={{ marginTop: 16 }}
      />
    ) : null;
  }

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
                strokeColor={record.chars > 5000 ? "var(--ts-color-warning)" : "var(--ts-color-info)"}
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

export default function SystemPromptTab() {
  const t = useT();
  const { token } = theme.useToken();
  const personasQuery = usePersonas();
  const promptFilesQuery = usePromptFiles();
  const { data: personas } = personasQuery;
  const { data: promptData } = promptFilesQuery;
  const [selectedPersona, setSelectedPersona] = useState<string | null>(null);
  const [editingFile, setEditingFile] = useState<{
    personaId: string;
    filename: string;
  } | null>(null);
  const [editContent, setEditContent] = useState<string | null>(null);
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

  const fileContentQuery = usePromptFileContent(
    editingFile?.personaId ?? null,
    editingFile?.filename ?? null,
  );
  const { data: fileContent, isLoading: contentLoading } = fileContentQuery;
  const updateMutation = useUpdatePromptFile();
  const previewQuery = usePromptPreview(
    previewOpen ? previewPersona : null,
  );
  const { data: previewData, isLoading: previewLoading } = previewQuery;

  const personaFiles = (promptFiles ?? []).filter(
    (f) => f.persona_id === activePersona,
  );

  const handleEdit = (personaId: string, filename: string) => {
    setEditingFile({ personaId, filename });
    setEditContent(null);
  };

  const handleSave = () => {
    if (!editingFile || !fileContent) return;
    updateMutation.mutate(
      {
        personaId: editingFile.personaId,
        filename: editingFile.filename,
        content: editContent ?? fileContent.content,
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

  const primaryError = personasQuery.error ?? promptFilesQuery.error;
  if (primaryError) {
    return (
      <PageQueryError
        error={primaryError}
        onRetry={() => {
          void personasQuery.refetch();
          void promptFilesQuery.refetch();
        }}
      />
    );
  }
  if (personasQuery.isLoading || promptFilesQuery.isLoading) {
    return <Spin />;
  }

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
            disabled={contentLoading || !fileContent}
            onClick={handleSave}
          >
            {t("button.save")}
          </Button>
        }
      >
        {fileContentQuery.error ? (
          <PageQueryError
            error={fileContentQuery.error}
            onRetry={() => void fileContentQuery.refetch()}
          />
        ) : contentLoading ? (
          <Spin />
        ) : (
          <Input.TextArea
            value={editContent ?? fileContent?.content ?? ""}
            onChange={(e) => setEditContent(e.target.value)}
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
        {previewQuery.error ? (
          <PageQueryError
            error={previewQuery.error}
            onRetry={() => void previewQuery.refetch()}
          />
        ) : previewLoading ? (
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
