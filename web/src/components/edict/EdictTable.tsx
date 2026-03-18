import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
import type { Edict, EdictStatus } from "../../api/types";
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
}

export default function EdictTable({
  edicts,
  total,
  page,
  pageSize,
  loading,
  onPageChange,
}: EdictTableProps) {
  const navigate = useNavigate();

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
      title: "旨意",
      dataIndex: "goal",
      ellipsis: true,
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
