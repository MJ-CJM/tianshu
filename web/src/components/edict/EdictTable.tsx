import { useState } from "react";
import { Button, Input, Popconfirm, Popover, Table, message, theme } from "antd";
import type { ColumnsType } from "antd/es/table";
import { DeleteOutlined, EditOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import type { Edict, EdictStatus } from "../../api/types";
import { updateEdict } from "../../api/edicts";
import MonoText from "../common/MonoText";
import SemanticTag from "../common/SemanticTag";
import { truncateId, formatTime } from "../../utils/format";
import { EDICT_STATUS_LABELS, EDICT_STATUS_COLORS, PRIORITY_LABELS, PRIORITY_COLORS } from "../../utils/constants";
import { useT } from "../../i18n";

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
}: EdictTableProps) {
  const t = useT();
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameSaving, setRenameSaving] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [batchDeleting, setBatchDeleting] = useState(false);

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0 || !onBatchDelete) return;
    setBatchDeleting(true);
    try {
      await onBatchDelete(selectedRowKeys as string[]);
      message.success(t("comp.edictTable.toastBatchDeleted", { n: selectedRowKeys.length }));
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
      render: (_: string, record: Edict) =>
        record.title || record.goal.slice(0, 20) + (record.goal.length > 20 ? "…" : ""),
    },
    {
      title: t("comp.edictTable.status"),
      dataIndex: "status",
      width: 100,
      render: (status: EdictStatus) => (
        <SemanticTag colorVar={EDICT_STATUS_COLORS[status]}>
          {EDICT_STATUS_LABELS[status]}
        </SemanticTag>
      ),
    },
    {
      title: t("comp.edictTable.priority"),
      dataIndex: "priority",
      width: 80,
      render: (priority: string) => (
        <SemanticTag colorVar={PRIORITY_COLORS[priority] ?? "var(--ts-color-info)"}>
          {PRIORITY_LABELS[priority] ?? priority}
        </SemanticTag>
      ),
    },
    {
      title: t("comp.edictTable.createdAt"),
      dataIndex: "created_at",
      width: 180,
      render: (v: string) => (
        <span style={{ color: token.colorTextSecondary, fontSize: 13 }}>{formatTime(v)}</span>
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
                  icon={<EditOutlined />}
                />
              </Popover>
            )}
            {canDelete && (
              <Popconfirm
                title={t("comp.edictTable.deleteConfirm")}
                description={t("comp.edictTable.deleteDesc")}
                onConfirm={(e) => handleDelete(e as unknown as React.MouseEvent, record.id)}
                onCancel={(e) => e?.stopPropagation()}
                okText={t("common.confirm")}
                cancelText={t("common.cancel")}
                onPopupClick={(e) => e.stopPropagation()}
              >
                <Button
                  type="text"
                  danger
                  size="small"
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
        <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ color: token.colorTextSecondary, fontSize: 13 }}>
            {t("comp.edictTable.selected", { n: selectedRowKeys.length })}
          </span>
          <Popconfirm
            title={t("comp.edictTable.batchDeleteConfirm", { n: selectedRowKeys.length })}
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
        size="middle"
      />
    </>
  );
}
