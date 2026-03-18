import { useState } from "react";
import { Button, Input, Popconfirm, Popover, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { DeleteOutlined, EditOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import type { Edict, EdictStatus } from "../../api/types";
import { updateEdict } from "../../api/edicts";
import MonoText from "../common/MonoText";
import { truncateId, formatTime } from "../../utils/format";
import { EDICT_STATUS_LABELS, EDICT_STATUS_COLORS } from "../../utils/constants";

interface EdictTableProps {
  edicts: Edict[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  onPageChange: (page: number, pageSize: number) => void;
  onDelete: (edictId: string) => Promise<void>;
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
  onRefresh,
}: EdictTableProps) {
  const navigate = useNavigate();
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameSaving, setRenameSaving] = useState(false);

  const handleDelete = async (e: React.MouseEvent, edictId: string) => {
    e.stopPropagation();
    try {
      await onDelete(edictId);
      message.success("敕令已删除");
    } catch {
      message.error("删除失败");
    }
  };

  const handleRename = async (edictId: string) => {
    const trimmed = renameValue.trim();
    if (!trimmed) return;
    setRenameSaving(true);
    try {
      await updateEdict(edictId, { title: trimmed });
      setRenameId(null);
      message.success("已重命名");
      onRefresh?.();
    } catch {
      message.error("重命名失败");
    } finally {
      setRenameSaving(false);
    }
  };

  const columns: ColumnsType<Edict> = [
    {
      title: "敕令编号",
      dataIndex: "id",
      width: 120,
      render: (id: string) => (
        <MonoText style={{ color: "#00d4ff", fontSize: 12 }}>
          {truncateId(id)}
        </MonoText>
      ),
    },
    {
      title: "卷宗名",
      dataIndex: "title",
      ellipsis: true,
      render: (_: string, record: Edict) =>
        record.title || record.goal.slice(0, 20) + (record.goal.length > 20 ? "…" : ""),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (status: EdictStatus) => (
        <Tag color={EDICT_STATUS_COLORS[status]}>
          {EDICT_STATUS_LABELS[status]}
        </Tag>
      ),
    },
    {
      title: "颁发时间",
      dataIndex: "created_at",
      width: 180,
      render: (v: string) => (
        <span style={{ color: "#94a3b8", fontSize: 13 }}>{formatTime(v)}</span>
      ),
    },
    {
      title: "操作",
      width: 120,
      render: (_, record) => {
        const canEdit = record.status === "open";
        const canDelete = record.status === "open" || record.status === "cancelled";
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
                      确定
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
                title="确认删除？"
                description="删除后关联的奏折和事件将一并清除"
                onConfirm={(e) => handleDelete(e as unknown as React.MouseEvent, record.id)}
                onCancel={(e) => e?.stopPropagation()}
                okText="确认"
                cancelText="取消"
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
    <Table<Edict>
      columns={columns}
      dataSource={edicts}
      rowKey="id"
      loading={loading}
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
  );
}
