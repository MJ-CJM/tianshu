import { useState, useMemo } from "react";
import { Button, Input, Select, Space, Typography } from "antd";
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
      title="敕令总览"
      extra={
        <Space>
          <Input
            prefix={<SearchOutlined />}
            placeholder="搜索敕令..."
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
      <div
        style={{
          padding: "20px 24px",
          marginBottom: 16,
          borderRadius: 8,
          background:
            "linear-gradient(135deg, rgba(255,247,230,0.6) 0%, rgba(241,245,255,0.6) 100%)",
          border: "1px solid rgba(217,178,136,0.25)",
        }}
      >
        <Typography.Paragraph
          style={{ margin: 0, fontSize: 15, lineHeight: 1.8 }}
        >
          天枢是一座会与你共同成长的宫殿。宫殿里有你的分身（emperor）——
          跨会话、跨平台持续演进的个人画像；也有六部官员 ——
          各自精进专业，共同辅佐你的目标。任务流转间，官员与分身一起成长。
        </Typography.Paragraph>
      </div>
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
