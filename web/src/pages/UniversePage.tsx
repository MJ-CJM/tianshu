import { useEffect, useState } from "react";
import { Button, Card, Input, Modal, Space, Table, Tag, Tree, message } from "antd";
import { ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { DataNode } from "antd/es/tree";
import {
  archiveUniverse,
  branchUniverse,
  deleteUniverse,
  enableParallelUniverse,
  getCodeDiff,
  getUniverseStatus,
  listEvalRuns,
  listUniverses,
  promoteCodeVariant,
  proposeAutoCode,
  proposeCodeVariant,
  restoreUniverse,
  switchUniverse,
  triggerEvolve,
} from "../api/universe";
import type { Universe, VariantEvalRun } from "../api/types";
import PageContainer from "../components/common/PageContainer";
import { useT } from "../i18n";

const STATUS_COLOR: Record<string, string> = {
  champion: "gold",
  challenger: "blue",
  archived: "default",
};

const STATUS_LABEL: Record<string, string> = {
  champion: "在役",
  challenger: "候选",
  archived: "已归档",
};

const ORIGIN_LABEL: Record<string, string> = {
  genesis: "创世",
  manual_branch: "手动分支",
  mutation: "演化变异",
  code_variant: "代码变体",
};

function buildLineage(rows: Universe[]): DataNode[] {
  const byParent = new Map<string | null, Universe[]>();
  rows.forEach((u) => {
    const k = u.parent_universe_id ?? null;
    byParent.set(k, [...(byParent.get(k) ?? []), u]);
  });
  const toNode = (u: Universe): DataNode => ({
    key: u.id,
    title: `${u.name} · ${STATUS_LABEL[u.status] ?? u.status} · score=${
      u.fitness?.score ?? "—"
    }`,
    children: (byParent.get(u.id) ?? []).map(toNode),
  });
  return (byParent.get(null) ?? []).map(toNode);
}

export default function UniversePage() {
  const t = useT();
  const [rows, setRows] = useState<Universe[]>([]);
  const [loading, setLoading] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [evolving, setEvolving] = useState(false);

  // 代码演化 modal state
  const [proposeOpen, setProposeOpen] = useState(false);
  const [proposing, setProposing] = useState(false);
  const [proposeTargetPath, setProposeTargetPath] = useState("src/tianshu/planner/");
  const [proposeHypothesis, setProposeHypothesis] = useState("");

  // 代码Diff modal state
  const [diffOpen, setDiffOpen] = useState(false);
  const [diffContent, setDiffContent] = useState("");
  const [diffLoading, setDiffLoading] = useState(false);

  // 评估记录 modal state
  const [evalOpen, setEvalOpen] = useState(false);
  const [evalRuns, setEvalRuns] = useState<VariantEvalRun[]>([]);
  const [evalLoading, setEvalLoading] = useState(false);

  // 晋升审批 modal state
  const [promoteReviewOpen, setPromoteReviewOpen] = useState(false);
  const [promoteTarget, setPromoteTarget] = useState<Universe | null>(null);
  const [promoting, setPromoting] = useState(false);

  // 自主提案
  const [autoProposing, setAutoProposing] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [listRes, statusRes] = await Promise.all([
        listUniverses(),
        getUniverseStatus(),
      ]);
      if (listRes.success && listRes.data) setRows(listRes.data);
      if (statusRes.success && statusRes.data) setEnabled(statusRes.data.enabled);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const onEnable = async () => {
    setEnabling(true);
    try {
      const res = await enableParallelUniverse();
      if (res.success) {
        void message.success("已开启平行位面，已创建创世位面");
        void load();
      }
    } finally {
      setEnabling(false);
    }
  };

  const onBranch = (u: Universe) => {
    let name = "";
    Modal.confirm({
      title: `从「${u.name}」分支新位面`,
      content: (
        <Input
          placeholder="位面名称"
          onChange={(e) => {
            name = e.target.value;
          }}
        />
      ),
      onOk: async () => {
        const res = await branchUniverse(u.id, name || "新位面");
        if (res.success) {
          void message.success("已分支");
          void load();
        }
      },
    });
  };

  const onSwitch = async (u: Universe) => {
    const res = await switchUniverse(u.id);
    if (res.success) {
      void message.success(`已切换到「${u.name}」`);
      void load();
    }
  };

  const onArchive = async (u: Universe) => {
    const res = await archiveUniverse(u.id);
    if (res.success) {
      void message.success("已归档");
      void load();
    }
  };

  const onRestore = async (u: Universe) => {
    const res = await restoreUniverse(u.id);
    if (res.success) {
      void message.success("已恢复");
      void load();
    }
  };

  const onEvolve = async () => {
    setEvolving(true);
    try {
      const res = await triggerEvolve();
      if (res.success && res.data) {
        const d = res.data;
        if (d.created_challenger) {
          void message.success("已演化出候选位面");
        } else if (d.promotion_recommended) {
          void message.info("有候选位面建议晋升");
        } else {
          void message.info("本轮无变更");
        }
        void load();
      }
    } finally {
      setEvolving(false);
    }
  };

  const onPropose = async () => {
    if (!proposeHypothesis.trim()) {
      void message.warning("请填写假设描述");
      return;
    }
    setProposing(true);
    try {
      const res = await proposeCodeVariant(proposeTargetPath, proposeHypothesis);
      if (!res.success || !res.data) {
        void message.error("提案失败");
        return;
      }
      const d = res.data;
      const status = d.status as string;
      const detail = d.detail as string | undefined;
      if (status === "disabled") {
        void message.warning("代码变体功能未开启（code_variant_enabled=False）");
      } else if (status === "no_champion") {
        void message.warning("无冠军位面");
      } else if (status === "no_mutation") {
        void message.info("未产生有效代码改动");
      } else if (status === "no_collaborators") {
        void message.warning("无协作者");
      } else if (status === "gate_failed") {
        void message.warning(`门禁未通过：${detail ?? ""}`);
      } else if (status === "recommended") {
        void message.success("评估通过且优于冠军，已推荐晋升");
      } else if (status === "evaluated") {
        void message.info("已评估，但未超过冠军 margin");
      } else {
        void message.error(`提案失败：${detail ?? status}`);
      }
      setProposeOpen(false);
      setProposeHypothesis("");
      void load();
    } finally {
      setProposing(false);
    }
  };

  const loadDiff = async (u: Universe) => {
    setDiffContent("");
    setDiffLoading(true);
    try {
      const res = await getCodeDiff(u.id);
      if (res.success && res.data) {
        setDiffContent(res.data.diff);
      }
    } finally {
      setDiffLoading(false);
    }
  };

  const loadEvalRuns = async (u: Universe) => {
    setEvalRuns([]);
    setEvalLoading(true);
    try {
      const res = await listEvalRuns(u.id);
      if (res.success && res.data) {
        setEvalRuns(res.data);
      }
    } finally {
      setEvalLoading(false);
    }
  };

  const onShowDiff = async (u: Universe) => {
    setDiffOpen(true);
    await loadDiff(u);
  };

  const onShowEvalRuns = async (u: Universe) => {
    setEvalOpen(true);
    await loadEvalRuns(u);
  };

  const onDelete = (u: Universe) => {
    Modal.confirm({
      title: `确认彻底删除「${u.name}」？此操作不可恢复。`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        const res = await deleteUniverse(u.id);
        if (res.success) {
          void message.success("已删除");
          void load();
        }
      },
    });
  };

  const onOpenPromoteReview = async (u: Universe) => {
    setPromoteTarget(u);
    setPromoteReviewOpen(true);
    await Promise.all([loadDiff(u), loadEvalRuns(u)]);
  };

  const onConfirmPromote = async () => {
    if (!promoteTarget) return;
    setPromoting(true);
    try {
      const res = await promoteCodeVariant(promoteTarget.id);
      if (res.success) {
        void message.success("已晋升为冠军并暂存部署指针(重启为受控步骤)");
        setPromoteReviewOpen(false);
        void load();
      } else {
        void message.error("晋升失败");
      }
    } finally {
      setPromoting(false);
    }
  };

  const onProposeAuto = async () => {
    setAutoProposing(true);
    try {
      const res = await proposeAutoCode();
      if (!res.success || !res.data) {
        void message.error("自主提案失败");
        return;
      }
      const d = res.data;
      const skipped = d.skipped as string | undefined;
      if (skipped) {
        void message.info(`跳过:${skipped}`);
      } else {
        void message.success(`已提案 ${(d.proposed as number | undefined) ?? 0} 个`);
      }
      void load();
    } finally {
      setAutoProposing(false);
    }
  };

  const evalColumns: ColumnsType<VariantEvalRun> = [
    {
      title: "门禁",
      dataIndex: "gate_passed",
      render: (v: boolean) => (
        <Tag color={v ? "green" : "red"}>{v ? "通过" : "未通过"}</Tag>
      ),
    },
    {
      title: "阶段",
      dataIndex: "gate_detail",
      render: (d: VariantEvalRun["gate_detail"]) => d?.stage ?? "—",
    },
    {
      title: "适应度",
      dataIndex: "fitness",
      render: (f: Record<string, number>) =>
        f && typeof f.score === "number" ? f.score.toFixed(3) : "—",
    },
    {
      title: "费用(CNY)",
      dataIndex: "cost",
      render: (c: number) => c.toFixed(4),
    },
    { title: "创建时间", dataIndex: "created_at" },
  ];

  const columns: ColumnsType<Universe> = [
    {
      title: "名称",
      dataIndex: "name",
      render: (name: string, u: Universe) =>
        u.status === "champion" ? (
          <Space>
            <span style={{ fontWeight: 600 }}>{name}</span>
            <Tag color="gold">当前</Tag>
          </Space>
        ) : (
          name
        ),
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] ?? "default"}>{STATUS_LABEL[s] ?? s}</Tag>
      ),
    },
    {
      title: "来源",
      dataIndex: "origin",
      render: (o: string) => (
        <Space size={4}>
          {o === "code_variant" ? (
            <Tag color="purple">代码位面</Tag>
          ) : (
            <Tag>配置位面</Tag>
          )}
          <span>{ORIGIN_LABEL[o] ?? o}</span>
        </Space>
      ),
    },
    {
      title: "适应度",
      dataIndex: "fitness",
      render: (f: Record<string, number>) =>
        f && typeof f.score === "number" ? f.score.toFixed(3) : "—",
    },
    { title: "创建时间", dataIndex: "created_at" },
    {
      title: "操作",
      render: (_: unknown, u: Universe) => {
        const isCodeVariant = u.code_ref !== null;
        if (isCodeVariant) {
          return (
            <Space>
              {u.status === "challenger" && (
                <>
                  <Button size="small" onClick={() => void onShowDiff(u)}>代码Diff</Button>
                  <Button size="small" onClick={() => void onShowEvalRuns(u)}>评估记录</Button>
                  <Button
                    size="small"
                    type="primary"
                    onClick={() => void onOpenPromoteReview(u)}
                  >
                    晋升代码
                  </Button>
                  <Button size="small" danger onClick={() => void onArchive(u)}>归档</Button>
                  <Button size="small" danger onClick={() => onDelete(u)}>删除</Button>
                </>
              )}
              {u.status === "archived" && (
                <>
                  <Button size="small" onClick={() => void onRestore(u)}>恢复</Button>
                  <Button size="small" onClick={() => void onShowEvalRuns(u)}>评估记录</Button>
                  <Button size="small" danger onClick={() => onDelete(u)}>删除</Button>
                </>
              )}
            </Space>
          );
        }
        // data universe: existing buttons
        return (
          <Space>
            {u.status !== "archived" && (
              <Button size="small" onClick={() => onBranch(u)}>分支</Button>
            )}
            {u.status === "challenger" && (
              <Button
                size="small"
                type="primary"
                onClick={() => void onSwitch(u)}
              >
                切换/回滚
              </Button>
            )}
            {u.status === "challenger" && (
              <Button size="small" danger onClick={() => void onArchive(u)}>
                归档
              </Button>
            )}
            {u.status === "challenger" && (
              <Button size="small" danger onClick={() => onDelete(u)}>
                删除
              </Button>
            )}
            {u.status === "archived" && (
              <Button size="small" onClick={() => void onRestore(u)}>恢复</Button>
            )}
            {u.status === "archived" && (
              <Button size="small" danger onClick={() => onDelete(u)}>删除</Button>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <PageContainer
      title={t("nav.universe")}
      extra={
        <Space>
          {!enabled && (
            <Button type="primary" loading={enabling} onClick={() => void onEnable()}>
              开启平行位面
            </Button>
          )}
          {enabled && (
            <>
              <Button
                onClick={() => setProposeOpen(true)}
              >
                代码演化
              </Button>
              <Button
                loading={autoProposing}
                onClick={() => void onProposeAuto()}
              >
                自主提案
              </Button>
              <Button
                icon={<ThunderboltOutlined />}
                loading={evolving}
                onClick={() => void onEvolve()}
              >
                演化
              </Button>
            </>
          )}
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            {t("action.refresh")}
          </Button>
        </Space>
      }
    >
      {rows.length > 0 && (
        <Card size="small" title="位面谱系" style={{ marginBottom: 16 }}>
          <Tree treeData={buildLineage(rows)} defaultExpandAll showLine />
        </Card>
      )}

      <Table<Universe>
        rowKey="id"
        dataSource={rows}
        loading={loading}
        pagination={false}
        columns={columns}
        size="middle"
        locale={{ emptyText: enabled ? "暂无位面" : "平行位面未开启，点击「开启平行位面」按钮开始" }}
      />

      {/* 代码演化 Modal */}
      <Modal
        title="代码演化"
        open={proposeOpen}
        onCancel={() => { setProposeOpen(false); setProposeHypothesis(""); }}
        onOk={() => void onPropose()}
        confirmLoading={proposing}
        okText="提交"
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <div>
            <div style={{ marginBottom: 4 }}>目标路径</div>
            <Input
              value={proposeTargetPath}
              onChange={(e) => setProposeTargetPath(e.target.value)}
              placeholder="例如：src/tianshu/planner/"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>假设描述</div>
            <Input.TextArea
              rows={4}
              value={proposeHypothesis}
              onChange={(e) => setProposeHypothesis(e.target.value)}
              placeholder="描述你希望对代码进行的改进假设..."
            />
          </div>
        </Space>
      </Modal>

      {/* 代码Diff Modal */}
      <Modal
        title="代码 Diff"
        open={diffOpen}
        onCancel={() => setDiffOpen(false)}
        footer={<Button onClick={() => setDiffOpen(false)}>关闭</Button>}
        width={800}
      >
        {diffLoading ? (
          <div>加载中...</div>
        ) : (
          <pre style={{ fontFamily: "monospace", fontSize: 12, whiteSpace: "pre-wrap", overflowX: "auto" }}>
            {diffContent || "无差异"}
          </pre>
        )}
      </Modal>

      {/* 评估记录 Modal */}
      <Modal
        title="评估记录"
        open={evalOpen}
        onCancel={() => setEvalOpen(false)}
        footer={<Button onClick={() => setEvalOpen(false)}>关闭</Button>}
        width={800}
      >
        <Table<VariantEvalRun>
          rowKey="id"
          dataSource={evalRuns}
          loading={evalLoading}
          columns={evalColumns}
          pagination={false}
          size="small"
          locale={{ emptyText: "暂无评估记录" }}
        />
      </Modal>

      {/* 晋升审批 Modal：diff + 评估记录(含基线/delta) 同屏，确认后才真正晋升 */}
      <Modal
        title={`晋升审批:${promoteTarget?.name ?? ""}`}
        open={promoteReviewOpen}
        onCancel={() => setPromoteReviewOpen(false)}
        width={960}
        footer={[
          <Button key="cancel" onClick={() => setPromoteReviewOpen(false)}>取消</Button>,
          <Button
            key="ok"
            type="primary"
            danger
            loading={promoting}
            onClick={() => void onConfirmPromote()}
          >
            确认晋升
          </Button>,
        ]}
      >
        <h4>代码改动(相对 fork 起点)</h4>
        <pre style={{ maxHeight: 320, overflow: "auto", background: "#f6f6f6", padding: 12 }}>
          {diffContent || "(无改动或加载中)"}
        </pre>
        <h4>评估记录(变体 vs 冠军基线,同评估集)</h4>
        <Table<VariantEvalRun>
          size="small"
          rowKey="id"
          pagination={false}
          dataSource={evalRuns}
          loading={evalLoading}
          columns={[
            { title: "时间", dataIndex: "created_at", width: 180 },
            {
              title: "门禁",
              dataIndex: "gate_passed",
              width: 70,
              render: (v: boolean) => (v ? <Tag color="green">过</Tag> : <Tag color="red">毙</Tag>),
            },
            { title: "变体分", width: 90, render: (_, r) => r.fitness?.score ?? "—" },
            { title: "基线分", width: 90, render: (_, r) => r.baseline?.score ?? "—" },
            {
              title: "delta",
              width: 90,
              render: (_, r) => {
                const v = r.fitness?.score;
                const b = r.baseline?.score;
                if (typeof v !== "number" || typeof b !== "number") return "—";
                const d = v - b;
                return <span style={{ color: d >= 0 ? "#3f8600" : "#cf1322" }}>{d.toFixed(4)}</span>;
              },
            },
            { title: "备注", render: (_, r) => (r.fitness?.truncated ? "预算截断" : "") },
          ]}
        />
        <p style={{ marginTop: 12, color: "#999" }}>
          晋升将翻转冠军并暂存部署指针;重启是单独受控步骤,健康检查失败会自动回滚。
        </p>
      </Modal>
    </PageContainer>
  );
}
