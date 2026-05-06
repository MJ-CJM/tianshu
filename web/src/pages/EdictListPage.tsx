import { useState, useMemo } from "react";
import { Button, Input, Select, Space } from "antd";
import { PlusOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { listEdicts, deleteEdict } from "../api/edicts";
import EdictTable from "../components/edict/EdictTable";
import PageContainer from "../components/common/PageContainer";
import { PAGE_SIZE, useEdictStatusLabels } from "../utils/constants";
import { useT } from "../i18n";

export default function EdictListPage() {
  const navigate = useNavigate();
  const t = useT();
  const edictStatusLabels = useEdictStatusLabels();
  const statusOptions = [
    { value: "", label: t("statusFilter.all") },
    ...Object.entries(edictStatusLabels).map(([value, label]) => ({
      value,
      label,
    })),
  ];
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("open");
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

  const handleBatchDelete = async (edictIds: string[]) => {
    await Promise.all(edictIds.map((id) => deleteEdict(id)));
    queryClient.invalidateQueries({ queryKey: ["edicts"] });
  };

  return (
    <PageContainer
      title={t("nav.edictList")}
      extra={
        <Space>
          <Input
            prefix={<SearchOutlined />}
            placeholder={t("form.search.edict")}
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
            {t("nav.edictCreate")}
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
        onBatchDelete={handleBatchDelete}
        onRefresh={() => queryClient.invalidateQueries({ queryKey: ["edicts"] })}
      />
    </PageContainer>
  );
}
