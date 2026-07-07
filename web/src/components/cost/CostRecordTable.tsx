import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { CostRecord } from "../../api/types";
import { useT } from "../../i18n";

interface Props {
  records: CostRecord[];
  total: number;
  loading: boolean;
  page: number;
  pageSize: number;
  onPageChange: (page: number, pageSize: number) => void;
}

export default function CostRecordTable({
  records,
  total,
  loading,
  page,
  pageSize,
  onPageChange,
}: Props) {
  const t = useT();
  const columns: ColumnsType<CostRecord> = [
    {
      title: t("cost.record.time"),
      dataIndex: "created_at",
      key: "created_at",
      width: 180,
      render: (v: string) => new Date(v).toLocaleString("zh-CN"),
    },
    {
      title: t("cost.record.edictId"),
      dataIndex: "edict_id",
      key: "edict_id",
      width: 160,
      ellipsis: true,
    },
    {
      title: t("cost.record.model"),
      dataIndex: "model",
      key: "model",
      width: 140,
      render: (v: string) => <Tag>{v || "default"}</Tag>,
    },
    {
      title: t("cost.record.promptTokens"),
      dataIndex: "prompt_tokens",
      key: "prompt_tokens",
      width: 100,
      align: "right",
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: t("cost.record.completionTokens"),
      dataIndex: "completion_tokens",
      key: "completion_tokens",
      width: 100,
      align: "right",
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: t("cost.record.totalTokens"),
      dataIndex: "total_tokens",
      key: "total_tokens",
      width: 100,
      align: "right",
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: t("cost.record.cost"),
      dataIndex: "cost_cny",
      key: "cost_cny",
      width: 120,
      align: "right",
      render: (v: number) => `¥${v.toFixed(4)}`,
    },
  ];

  return (
    <Table<CostRecord>
      columns={columns}
      dataSource={records}
      rowKey="id"
      loading={loading}
      size="small"
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        onChange: onPageChange,
      }}
    />
  );
}
