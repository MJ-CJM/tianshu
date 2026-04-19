import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Spin,
  Empty,
  Tag,
  Space,
  Alert,
  Button,
  Select,
  Modal,
  Input,
  notification,
} from "antd";
import {
  ReloadOutlined,
  EditOutlined,
} from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { usePersonaProfile } from "../../hooks/usePersonaProfile";
import { getProfileHistory, updateProfileManual } from "../../api/profile";

interface Props {
  personaId: string;
}

export default function ProfileTab({ personaId }: Props) {
  const { data, isLoading, error } = usePersonaProfile(personaId);
  const qc = useQueryClient();

  const [syncing, setSyncing] = useState(false);
  const [syncStatus, setSyncStatus] = useState<string | null>(null);
  const [historyModal, setHistoryModal] = useState<{
    name: string;
    markdown: string;
  } | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualDraft, setManualDraft] = useState("");
  const [manualSaving, setManualSaving] = useState(false);

  async function handleSynthesize() {
    setSyncing(true);
    setSyncStatus("started");
    try {
      const resp = await fetch(`/api/personas/${personaId}/synthesize`, {
        method: "POST",
      });
      if (!resp.ok || !resp.body) {
        notification.error({
          message: "合成失败",
          description: `HTTP ${resp.status}`,
        });
        setSyncStatus(null);
        setSyncing(false);
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      const TERMINAL = new Set([
        "profile.synthesis.completed",
        "profile.synthesis.degraded",
        "profile.synthesis.failed",
        "profile.synthesis.skipped",
      ]);
      let terminal: string | null = null;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const blocks = buf.split("\n\n");
        buf = blocks.pop() ?? "";
        for (const block of blocks) {
          const m = block.match(/event: ([^\n]+)/);
          if (m && m[1]) {
            const evt = m[1].trim();
            setSyncStatus(evt);
            if (TERMINAL.has(evt)) {
              terminal = evt;
            }
          }
        }
        if (terminal) break;
      }
      if (terminal === "profile.synthesis.completed") {
        notification.success({ message: "合成完成" });
      } else if (terminal === "profile.synthesis.degraded") {
        notification.warning({ message: "合成完成(降级模式)" });
      } else if (terminal === "profile.synthesis.failed") {
        notification.error({ message: "合成失败" });
      } else if (terminal === "profile.synthesis.skipped") {
        notification.info({ message: "已跳过(并发运行中)" });
      }
      await qc.invalidateQueries({
        queryKey: ["persona", "profile", personaId],
      });
    } finally {
      setSyncing(false);
      setTimeout(() => setSyncStatus(null), 3000);
    }
  }

  async function handleHistoryChange(value: string | undefined) {
    if (!value) return;
    const m = value.match(/^v(\d+)-/);
    if (!m) return;
    const version = Number(m[1]);
    setHistoryLoading(true);
    try {
      const resp = await getProfileHistory(personaId, version);
      if (resp.success && resp.data) {
        setHistoryModal({ name: resp.data.name, markdown: resp.data.markdown });
      }
    } finally {
      setHistoryLoading(false);
    }
  }

  function openManualEdit() {
    setManualDraft(data?.manual_section ?? "");
    setManualOpen(true);
  }

  async function handleManualSave() {
    setManualSaving(true);
    try {
      const resp = await updateProfileManual(personaId, manualDraft);
      if (resp.success) {
        notification.success({ message: "手写段已保存" });
        setManualOpen(false);
        await qc.invalidateQueries({
          queryKey: ["persona", "profile", personaId],
        });
      }
    } finally {
      setManualSaving(false);
    }
  }

  if (isLoading) {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <Spin tip="加载成长档案…" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="加载失败"
        description={String((error as Error).message ?? error)}
        style={{ margin: 16 }}
      />
    );
  }

  if (!data?.exists) {
    return (
      <Empty
        description='暂无成长档案。首版将在 AGENT_END 每 20 次 或每日 03:00 自动生成；也可点击"立即合成"。'
        style={{ padding: 48 }}
      />
    );
  }

  const fm = data.frontmatter;
  const degraded = !!fm?.degraded;

  return (
    <div style={{ padding: 16 }}>
      <Space wrap style={{ marginBottom: 12 }}>
        <Button
          icon={<ReloadOutlined spin={syncing} />}
          loading={syncing}
          onClick={handleSynthesize}
        >
          立即合成
          {syncStatus
            ? ` (${syncStatus.replace("profile.synthesis.", "")})`
            : ""}
        </Button>
        <Select
          placeholder="📜 历史版本"
          style={{ minWidth: 180 }}
          loading={historyLoading}
          onChange={handleHistoryChange}
          allowClear
          options={data.history.map((h) => ({ label: h.name, value: h.name }))}
        />
        <Button icon={<EditOutlined />} onClick={openManualEdit}>
          编辑手写
        </Button>
      </Space>

      <Space wrap style={{ marginBottom: 16 }}>
        {fm && <Tag color="blue">v{fm.version}</Tag>}
        {fm?.data_window && <Tag>window {fm.data_window}</Tag>}
        {fm?.last_synthesized && (
          <Tag color="default">合成于 {fm.last_synthesized.slice(0, 10)}</Tag>
        )}
        {fm?.synthesizer_model && (
          <Tag color="purple">{fm.synthesizer_model}</Tag>
        )}
        {fm?.manually_edited && <Tag color="cyan">用户手改</Tag>}
        {degraded && <Tag color="warning">⚠️ 降级</Tag>}
      </Space>

      <div className="tianshu-markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {data.markdown}
        </ReactMarkdown>
      </div>

      <Modal
        open={!!historyModal}
        title={historyModal?.name}
        width={900}
        onCancel={() => setHistoryModal(null)}
        footer={null}
      >
        <div className="tianshu-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {historyModal?.markdown ?? ""}
          </ReactMarkdown>
        </div>
      </Modal>

      <Modal
        open={manualOpen}
        title="编辑手写段(synthesizer 不会覆盖)"
        width={700}
        okText="保存"
        cancelText="取消"
        onOk={handleManualSave}
        onCancel={() => setManualOpen(false)}
        confirmLoading={manualSaving}
      >
        <Input.TextArea
          rows={16}
          value={manualDraft}
          onChange={(e) => setManualDraft(e.target.value)}
          placeholder="在这里写下你希望补充的内容(Markdown 支持)"
        />
      </Modal>
    </div>
  );
}
