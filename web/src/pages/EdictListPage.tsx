import { useState } from "react";
import { Button, Select, Space } from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { listEdicts } from "../api/edicts";
import EdictTable from "../components/edict/EdictTable";
import PageContainer from "../components/common/PageContainer";
import { PAGE_SIZE, EDICT_STATUS_LABELS } from "../utils/constants";

const statusOptions = [
  { value: "", label: "全部状态" },
  ...Object.entries(EDICT_STATUS_LABELS).map(([value, label]) => ({
    value,
    label,
  })),
];

export default function EdictListPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("");

  const edictsQuery = useQuery({
    queryKey: ["edicts", page, statusFilter],
    queryFn: () =>
      listEdicts({
        status: statusFilter || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
  });

  const edicts = edictsQuery.data?.data ?? [];
  const total = edictsQuery.data?.metadata?.total ?? 0;

  return (
    <PageContainer
      title="敕令卷宗"
      extra={
        <Space>
          <Select
            value={statusFilter}
            onChange={(v) => {
              setStatusFilter(v);
              setPage(1);
            }}
            options={statusOptions}
            style={{ width: 140 }}
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => edictsQuery.refetch()}
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => navigate("/edicts/create")}
          >
            颁发敕令
          </Button>
        </Space>
      }
    >
      <EdictTable
        edicts={edicts}
        total={total}
        page={page}
        pageSize={PAGE_SIZE}
        loading={edictsQuery.isLoading}
        onPageChange={(p) => setPage(p)}
      />
    </PageContainer>
  );
}
