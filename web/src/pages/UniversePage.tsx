import { useEffect, useState } from "react";
import { Button, Input, Modal, Space, Table, Tag, message } from "antd";
import { ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  archiveUniverse,
  branchUniverse,
  enableParallelUniverse,
  getUniverseStatus,
  listUniverses,
  restoreUniverse,
  switchUniverse,
  triggerEvolve,
} from "../api/universe";
import type { Universe } from "../api/types";
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

export default function UniversePage() {
  const t = useT();
  const [rows, setRows] = useState<Universe[]>([]);
  const [loading, setLoading] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [evolving, setEvolving] = useState(false);

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
    { title: "来源", dataIndex: "origin" },
    {
      title: "适应度",
      dataIndex: "fitness",
      render: (f: Record<string, number>) =>
        f && typeof f.score === "number" ? f.score.toFixed(3) : "—",
    },
    { title: "创建时间", dataIndex: "created_at" },
    {
      title: "操作",
      render: (_: unknown, u: Universe) => (
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
          {u.status === "archived" && (
            <Button size="small" onClick={() => void onRestore(u)}>恢复</Button>
          )}
        </Space>
      ),
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
            <Button
              icon={<ThunderboltOutlined />}
              loading={evolving}
              onClick={() => void onEvolve()}
            >
              演化
            </Button>
          )}
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            {t("action.refresh")}
          </Button>
        </Space>
      }
    >
      <Table<Universe>
        rowKey="id"
        dataSource={rows}
        loading={loading}
        pagination={false}
        columns={columns}
        size="middle"
        locale={{ emptyText: enabled ? "暂无位面" : "平行位面未开启，点击「开启平行位面」按钮开始" }}
      />
    </PageContainer>
  );
}
