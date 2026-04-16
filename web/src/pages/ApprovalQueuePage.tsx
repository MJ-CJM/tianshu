import { useState, useMemo } from "react";
import { Button, Empty, Input, Select, Space } from "antd";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import {
  useOpenEdicts,
  useEdictLatestMemorials,
  usePendingToolCalls,
} from "../hooks/useApprovals";
import DecreeModal from "../components/decree/DecreeModal";
import EdictActivityCard from "../components/decree/EdictActivityCard";
import PageContainer from "../components/common/PageContainer";
import {
  deriveEdictPhase,
  PHASE_SORT_ORDER,
  PHASE_LABELS,
  type EdictPhase,
} from "../utils/edictPhase";
import type { Memorial, PendingToolCall } from "../api/types";

const phaseOptions = [
  { value: "", label: "全部阶段" },
  ...Object.entries(PHASE_LABELS).map(([value, label]) => ({ value, label })),
];

export default function ApprovalQueuePage() {
  const { data, isLoading, refetch } = useOpenEdicts();
  const edicts = data?.data ?? [];
  const edictIds = useMemo(() => edicts.map((e) => e.id), [edicts]);

  const memorialQueries = useEdictLatestMemorials(edictIds);
  const { data: pendingToolCalls = [] } = usePendingToolCalls();

  const pendingByEdict = useMemo(() => {
    const map = new Map<string, PendingToolCall[]>();
    for (const p of pendingToolCalls) {
      const list = map.get(p.edict_id) ?? [];
      list.push(p);
      map.set(p.edict_id, list);
    }
    return map;
  }, [pendingToolCalls]);

  const [searchText, setSearchText] = useState("");
  const [phaseFilter, setPhaseFilter] = useState<EdictPhase | "">("");

  const [modalMemorial, setModalMemorial] = useState<Memorial | null>(null);
  const [modalAction, setModalAction] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const openModal = (memorial: Memorial, action: string) => {
    setModalMemorial(memorial);
    setModalAction(action);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setModalMemorial(null);
    setModalAction(null);
  };

  const items = useMemo(() => {
    const all = edicts.map((edict, i) => {
      const query = memorialQueries[i];
      const latestMemorial = query?.data?.data ?? null;
      const phase = deriveEdictPhase(latestMemorial);
      const pending = pendingByEdict.get(edict.id) ?? [];
      return { edict, latestMemorial, phase, pending };
    });

    const keyword = searchText.trim().toLowerCase();

    return all
      .filter((item) => {
        if (phaseFilter && item.phase !== phaseFilter) return false;
        if (keyword) {
          const titleMatch = item.edict.title.toLowerCase().includes(keyword);
          const goalMatch = item.edict.goal.toLowerCase().includes(keyword);
          const idMatch = item.edict.id.toLowerCase().includes(keyword);
          if (!titleMatch && !goalMatch && !idMatch) return false;
        }
        return true;
      })
      .sort((a, b) => {
        // Pending tool approvals take priority over phase order
        const ap = a.pending.length > 0 ? 1 : 0;
        const bp = b.pending.length > 0 ? 1 : 0;
        if (ap !== bp) return bp - ap;
        return PHASE_SORT_ORDER[a.phase] - PHASE_SORT_ORDER[b.phase];
      });
  }, [edicts, memorialQueries, pendingByEdict, searchText, phaseFilter]);

  return (
    <PageContainer
      title={`批红台 (${edicts.length})`}
      extra={
        <Space>
          <Input
            prefix={<SearchOutlined />}
            placeholder="搜索敕令..."
            allowClear
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            style={{ width: 200 }}
          />
          <Select
            value={phaseFilter}
            onChange={setPhaseFilter}
            options={phaseOptions}
            style={{ width: 130 }}
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => refetch()}
            loading={isLoading}
          />
        </Space>
      }
    >
      {items.length === 0 && !isLoading ? (
        <Empty
          description={
            searchText || phaseFilter ? "无匹配的敕令" : "暂无进行中的敕令"
          }
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {items.map(({ edict, latestMemorial, pending }) => (
            <EdictActivityCard
              key={edict.id}
              edict={edict}
              latestMemorial={latestMemorial}
              onOpenDecree={openModal}
              pendingToolCalls={pending}
            />
          ))}
        </div>
      )}

      <DecreeModal
        memorial={modalMemorial}
        action={modalAction}
        open={modalOpen}
        onClose={closeModal}
      />
    </PageContainer>
  );
}
