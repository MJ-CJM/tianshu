import { useState } from "react";
import {
  Button,
  Input,
  Popconfirm,
  Popover,
  Space,
  Table,
  message,
  theme,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { DeleteOutlined, EditOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import type { Edict, Memorial } from "../../api/types";
import { updateEdict } from "../../api/edicts";
import MonoText from "../common/MonoText";
import SemanticTag from "../common/SemanticTag";
import { truncateId, formatTime } from "../../utils/format";
import { PRIORITY_LABELS, PRIORITY_COLORS } from "../../utils/constants";
import { useT } from "../../i18n";
import {
  deriveEdictWorkspacePhase,
  getEdictTaskKinds,
  type EdictTaskKind,
  type EdictWorkspacePhase,
} from "../../utils/edictPresentation";

interface EdictTableProps {
  edicts: Edict[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  onPageChange: (page: number, pageSize: number) => void;
  onDelete: (edictId: string) => Promise<void>;
  onBatchDelete?: (edictIds: string[]) => Promise<void>;
  onRefresh?: () => void;
  latestMemorials?: Record<string, Memorial | null>;
  pendingDecisionCounts?: Record<string, number>;
  progressUnavailable?: boolean;
}

export default function EdictTable({
  edicts,
  total,
  page,
  pageSize,
  loading,
  onPageChange,
  onDelete,
  onBatchDelete,
  onRefresh,
  latestMemorials = {},
  pendingDecisionCounts = {},
  progressUnavailable = false,
}: EdictTableProps) {
  const t = useT();
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameSaving, setRenameSaving] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [batchDeleting, setBatchDeleting] = useState(false);
  const taskKindLabels: Record<EdictTaskKind, string> = {
    immediate: t("taskKind.immediate"),
    scheduled_once: t("taskKind.scheduledOnce"),
    recurring: t("taskKind.recurring"),
    long_running: t("taskKind.longRunning"),
    conversation: t("taskKind.conversation"),
    keqing: t("taskKind.keqing"),
  };
  const workspacePhaseLabels: Record<EdictWorkspacePhase, string> = {
    submitted: t("status.submitted"),
    running: t("status.running"),
    completed: t("status.completed"),
    failed: t("status.failed"),
    cancelled: t("status.cancelled"),
    scheduled: t("status.scheduled"),
    planning: t("status.planning"),
    auditing: t("status.auditing"),
    needs_review: t("status.needs_review"),
    paused: t("phase.paused"),
    winding_down: t("phase.windingDown"),
    idle: t("phase.idle"),
    no_memorial: t("phase.no_memorial"),
  };
  const workspacePhaseColors: Record<EdictWorkspacePhase, string> = {
    submitted: "var(--ts-status-submitted)",
    running: "var(--ts-status-running)",
    completed: "var(--ts-status-completed)",
    failed: "var(--ts-status-failed)",
    cancelled: "var(--ts-status-cancelled)",
    scheduled: "var(--ts-status-scheduled)",
    planning: "var(--ts-status-planning)",
    auditing: "var(--ts-status-auditing)",
    needs_review: "var(--ts-status-needs-review)",
    paused: "var(--ts-status-scheduled)",
    winding_down: "var(--ts-status-auditing)",
    idle: "var(--ts-status-completed)",
    no_memorial: "var(--ts-status-submitted)",
  };
  const taskKindColors: Record<EdictTaskKind, string> = {
    immediate: "var(--ts-color-info)",
    scheduled_once: "var(--ts-status-scheduled)",
    recurring: "var(--ts-color-accent)",
    long_running: "var(--ts-color-warning)",
    conversation: "var(--ts-color-success)",
    keqing: "var(--ts-color-accent)",
  };

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0 || !onBatchDelete) return;
    setBatchDeleting(true);
    try {
      await onBatchDelete(selectedRowKeys as string[]);
      message.success(
        t("comp.edictTable.toastBatchDeleted", { n: selectedRowKeys.length }),
      );
      setSelectedRowKeys([]);
    } catch {
      message.error(t("comp.edictTable.toastBatchDeleteFailed"));
    } finally {
      setBatchDeleting(false);
    }
  };

  const deletableIds = new Set(edicts.map((e) => e.id));

  const handleDelete = async (e: React.MouseEvent, edictId: string) => {
    e.stopPropagation();
    try {
      await onDelete(edictId);
      message.success(t("comp.edictTable.toastDeleted"));
    } catch {
      message.error(t("comp.edictTable.toastDeleteFailed"));
    }
  };

  const handleRename = async (edictId: string) => {
    const trimmed = renameValue.trim();
    if (!trimmed) return;
    setRenameSaving(true);
    try {
      await updateEdict(edictId, { title: trimmed });
      setRenameId(null);
      message.success(t("comp.edictTable.toastRenamed"));
      onRefresh?.();
    } catch {
      message.error(t("comp.edictTable.toastRenameFailed"));
    } finally {
      setRenameSaving(false);
    }
  };

  const columns: ColumnsType<Edict> = [
    {
      title: t("comp.edictTable.id"),
      dataIndex: "id",
      width: 120,
      render: (id: string) => (
        <MonoText style={{ color: token.colorInfo, fontSize: 12 }}>
          {truncateId(id)}
        </MonoText>
      ),
    },
    {
      title: t("comp.edictTable.title"),
      dataIndex: "title",
      ellipsis: true,
      render: (_: string, record: Edict) => (
        <Link
          to={`/edicts/${record.id}`}
          onClick={(event) => event.stopPropagation()}
        >
          {record.title ||
            record.goal.slice(0, 20) + (record.goal.length > 20 ? "…" : "")}
        </Link>
      ),
    },
    {
      title: t("comp.edictTable.taskType"),
      width: 210,
      render: (_, record) => (
        <Space size={[4, 4]} wrap>
          {getEdictTaskKinds(record).map((kind) => (
            <SemanticTag key={kind} colorVar={taskKindColors[kind]}>
              {taskKindLabels[kind]}
            </SemanticTag>
          ))}
        </Space>
      ),
    },
    {
      title: t("comp.edictTable.progress"),
      width: 120,
      render: (_, record) => {
        if (progressUnavailable && record.status === "open") {
          return (
            <SemanticTag colorVar="var(--ts-color-text-secondary)">
              {t("phase.unavailable")}
            </SemanticTag>
          );
        }
        const phase = deriveEdictWorkspacePhase(
          record,
          latestMemorials[record.id] ?? null,
          pendingDecisionCounts[record.id] ?? 0,
        );
        return (
          <SemanticTag
            colorVar={workspacePhaseColors[phase]}
            solid={phase === "needs_review"}
          >
            {workspacePhaseLabels[phase]}
          </SemanticTag>
        );
      },
    },
    {
      title: t("comp.edictTable.priority"),
      dataIndex: "priority",
      width: 80,
      render: (priority: string) => (
        <SemanticTag
          colorVar={PRIORITY_COLORS[priority] ?? "var(--ts-color-info)"}
        >
          {PRIORITY_LABELS[priority] ?? priority}
        </SemanticTag>
      ),
    },
    {
      title: t("comp.edictTable.createdAt"),
      dataIndex: "created_at",
      width: 180,
      render: (v: string) => (
        <span style={{ color: token.colorTextSecondary, fontSize: 13 }}>
          {formatTime(v)}
        </span>
      ),
    },
    {
      title: t("comp.edictTable.actions"),
      width: 120,
      render: (_, record) => {
        const canEdit = record.status === "open";
        const canDelete = true;
        return (
          <span onClick={(e) => e.stopPropagation()}>
            {canEdit && (
              <Popover
                trigger="click"
                open={renameId === record.id}
                onOpenChange={(open) => {
                  if (open) {
                    setRenameId(record.id);
                    setRenameValue(record.title || record.goal.slice(0, 20));
                  } else {
                    setRenameId(null);
                  }
                }}
                content={
                  <div style={{ display: "flex", gap: 8 }}>
                    <Input
                      size="small"
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onPressEnter={() => handleRename(record.id)}
                      style={{ width: 200 }}
                      autoFocus
                    />
                    <Button
                      size="small"
                      type="primary"
                      loading={renameSaving}
                      onClick={() => handleRename(record.id)}
                    >
                      {t("action.ok")}
                    </Button>
                  </div>
                }
              >
                <Button
                  type="text"
                  size="small"
                  aria-label={t("action.edit")}
                  icon={<EditOutlined />}
                />
              </Popover>
            )}
            {canDelete && (
              <Popconfirm
                title={t("comp.edictTable.deleteConfirm")}
                description={t("comp.edictTable.deleteDesc")}
                onConfirm={(e) =>
                  handleDelete(e as unknown as React.MouseEvent, record.id)
                }
                onCancel={(e) => e?.stopPropagation()}
                okText={t("common.confirm")}
                cancelText={t("common.cancel")}
                onPopupClick={(e) => e.stopPropagation()}
              >
                <Button
                  type="text"
                  danger
                  size="small"
                  aria-label={t("action.delete")}
                  icon={<DeleteOutlined />}
                />
              </Popconfirm>
            )}
          </span>
        );
      },
    },
  ];

  return (
    <>
      {selectedRowKeys.length > 0 && (
        <div
          style={{
            marginBottom: 12,
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <span style={{ color: token.colorTextSecondary, fontSize: 13 }}>
            {t("comp.edictTable.selected", { n: selectedRowKeys.length })}
          </span>
          <Popconfirm
            title={t("comp.edictTable.batchDeleteConfirm", {
              n: selectedRowKeys.length,
            })}
            description={t("comp.edictTable.deleteDesc")}
            onConfirm={handleBatchDelete}
            okText={t("common.confirm")}
            cancelText={t("common.cancel")}
          >
            <Button
              danger
              size="small"
              icon={<DeleteOutlined />}
              loading={batchDeleting}
            >
              {t("comp.edictTable.batchDelete")}
            </Button>
          </Popconfirm>
          <Button size="small" onClick={() => setSelectedRowKeys([])}>
            {t("comp.edictTable.clearSelection")}
          </Button>
        </div>
      )}
      <Table<Edict>
        columns={columns}
        dataSource={edicts}
        rowKey="id"
        loading={loading}
        rowSelection={{
          selectedRowKeys,
          onChange: setSelectedRowKeys,
          getCheckboxProps: (record) => ({
            disabled: !deletableIds.has(record.id),
            // a11y：每行的选择框必须有可读名称，否则屏幕阅读器只念"复选框"，
            // 用户无从知道选的是哪一行（axe label 规则判 serious）。表头的全选
            // 框由 antd 自带 aria-label，只有行内这个需要我们补。
            "aria-label": t("comp.edictTable.selectRow", {
              name: record.title || record.goal.slice(0, 20),
            }),
          }),
        }}
        onRow={(record) => ({
          onClick: () => navigate(`/edicts/${record.id}`),
          style: { cursor: "pointer" },
        })}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: false,
          onChange: onPageChange,
        }}
        scroll={{ x: 1120 }}
        size="middle"
      />
    </>
  );
}
