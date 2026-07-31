import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Input, Select, Space, Typography } from "antd";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { listEdicts, deleteEdict } from "../../api/edicts";
import {
  useEdictLatestMemorials,
  usePendingDecisions,
  usePendingToolCalls,
} from "../../hooks/useApprovals";
import EdictTable from "../edict/EdictTable";
import { PAGE_SIZE, useEdictStatusLabels } from "../../utils/constants";
import { useT } from "../../i18n";
import { toApiProblem } from "../../api/client";
import PageDataState from "../states/PageDataState";
import { problemPageStatus } from "../states/problemPageStatus";

export default function AllEdictsView() {
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
  const [statusFilter, setStatusFilter] = useState("");
  const [searchText, setSearchText] = useState("");
  const [searchValue, setSearchValue] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearchValue(searchText);
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchText]);

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
  const edicts = useMemo(
    () => edictsQuery.data?.data ?? [],
    [edictsQuery.data?.data],
  );
  const total = edictsQuery.data?.metadata?.total ?? 0;
  const edictIds = useMemo(() => edicts.map((edict) => edict.id), [edicts]);
  const memorialsQuery = useEdictLatestMemorials(edictIds, edictIds.length > 0);
  const pendingToolsQuery = usePendingToolCalls();
  const pendingDecisionsQuery = usePendingDecisions();
  const pendingDecisionCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const decision of pendingDecisionsQuery.data ?? []) {
      counts[decision.edict_id] = (counts[decision.edict_id] ?? 0) + 1;
    }
    for (const toolCall of pendingToolsQuery.data ?? []) {
      counts[toolCall.edict_id] = Math.max(counts[toolCall.edict_id] ?? 0, 1);
    }
    return counts;
  }, [pendingDecisionsQuery.data, pendingToolsQuery.data]);
  const enrichmentError =
    memorialsQuery.error ||
    pendingToolsQuery.error ||
    pendingDecisionsQuery.error;
  const refetchAll = () => {
    void edictsQuery.refetch();
    void memorialsQuery.refetch();
    void pendingToolsQuery.refetch();
    void pendingDecisionsQuery.refetch();
  };

  const handleDelete = async (edictId: string) => {
    await deleteEdict(edictId);
    queryClient.invalidateQueries({ queryKey: ["edicts"] });
  };

  const handleBatchDelete = async (edictIds: string[]) => {
    await Promise.all(edictIds.map((id) => deleteEdict(id)));
    queryClient.invalidateQueries({ queryKey: ["edicts"] });
  };

  if (edictsQuery.error) {
    const problem = toApiProblem(edictsQuery.error);
    return (
      <PageDataState
        status={problemPageStatus(problem)}
        data={null}
        problem={problem}
        isEmpty={(items: typeof edicts) => items.length === 0}
        onRetry={() => void edictsQuery.refetch()}
      >
        {() => null}
      </PageDataState>
    );
  }

  return (
    <>
      {enrichmentError && (
        <Alert
          type="warning"
          showIcon
          message={t("study.partialDataTitle")}
          description={t("study.partialDataDescription")}
          style={{ marginBottom: 16 }}
        />
      )}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <Typography.Text type="secondary">
          {t("study.workspaceSummary", { n: total })}
        </Typography.Text>
        <Space wrap>
          <Input
            prefix={<SearchOutlined />}
            placeholder={t("form.search.edict")}
            allowClear
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            style={{ width: 240, maxWidth: "100%" }}
          />
          <Select
            aria-label={t("comp.edictTable.status")}
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
            aria-label={t("action.refresh")}
            onClick={refetchAll}
            loading={
              edictsQuery.isFetching ||
              memorialsQuery.isFetching ||
              pendingToolsQuery.isFetching ||
              pendingDecisionsQuery.isFetching
            }
          />
        </Space>
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
        onRefresh={refetchAll}
        latestMemorials={memorialsQuery.data?.data ?? {}}
        pendingDecisionCounts={pendingDecisionCounts}
        progressUnavailable={Boolean(memorialsQuery.error)}
      />
    </>
  );
}
