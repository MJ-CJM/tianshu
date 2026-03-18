import { useState, useMemo } from "react";
import { Button, Input, Select, Space } from "antd";
import { PlusOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { listEdicts, deleteEdict } from "../api/edicts";
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
  const [searchText, setSearchText] = useState("");
  const [searchValue, setSearchValue] = useState("");

  const debouncedSearch = useMemo(() => {
    let timer: ReturnType<typeof setTimeout>;
    return (value: string) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        setSearchValue(value);
        setPage(1);
      }, 300);
    };
  }, []);

  const edictsQuery = useQuery({
    queryKey: ["edicts", page, statusFilter, searchValue],
    queryFn: () =>
      listEdicts({
        status: statusFilter || undefined,
        search: searchValue || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
  });

  const queryClient = useQueryClient();
  const edicts = edictsQuery.data?.data ?? [];
  const total = edictsQuery.data?.metadata?.total ?? 0;

  const handleDelete = async (edictId: string) => {
    await deleteEdict(edictId);
    queryClient.invalidateQueries({ queryKey: ["edicts"] });
  };

  return (
    <PageContainer
      title="敕令卷宗"
      extra={
        <Space>
          <Input
            prefix={<SearchOutlined />}
            placeholder="搜索卷宗..."
            allowClear
            value={searchText}
            onChange={(e) => {
              setSearchText(e.target.value);
              debouncedSearch(e.target.value);
            }}
            style={{ width: 200 }}
          />
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
        onDelete={handleDelete}
        onRefresh={() => queryClient.invalidateQueries({ queryKey: ["edicts"] })}
      />
    </PageContainer>
  );
}
