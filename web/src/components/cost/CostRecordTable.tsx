import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { CostRecord } from "../../api/types";

interface Props {
  records: CostRecord[];
  total: number;
  loading: boolean;
  page: number;
  pageSize: number;
  onPageChange: (page: number, pageSize: number) => void;
}

const columns: ColumnsType<CostRecord> = [
  {
    title: "时间",
    dataIndex: "created_at",
    key: "created_at",
    width: 180,
    render: (v: string) => new Date(v).toLocaleString("zh-CN"),
  },
  {
    title: "敕令编号",
    dataIndex: "edict_id",
    key: "edict_id",
    width: 160,
    ellipsis: true,
  },
  {
    title: "模型",
    dataIndex: "model",
    key: "model",
    width: 140,
    render: (v: string) => <Tag>{v || "default"}</Tag>,
  },
  {
    title: "输入",
    dataIndex: "prompt_tokens",
    key: "prompt_tokens",
    width: 100,
    align: "right",
    render: (v: number) => v.toLocaleString(),
  },
  {
    title: "输出",
    dataIndex: "completion_tokens",
    key: "completion_tokens",
    width: 100,
    align: "right",
    render: (v: number) => v.toLocaleString(),
  },
  {
    title: "总量",
    dataIndex: "total_tokens",
    key: "total_tokens",
    width: 100,
    align: "right",
    render: (v: number) => v.toLocaleString(),
  },
  {
    title: "成本 (CNY)",
    dataIndex: "cost_cny",
    key: "cost_cny",
    width: 120,
    align: "right",
    render: (v: number) => `¥${v.toFixed(4)}`,
  },
];

export default function CostRecordTable({
  records,
  total,
  loading,
  page,
  pageSize,
  onPageChange,
}: Props) {
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
