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
import { useT } from "../../i18n";
import { authFetch } from "../../api/authFetch";

interface Props {
  personaId: string;
}

export default function ProfileTab({ personaId }: Props) {
  const t = useT();
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
      const resp = await authFetch(`/api/personas/${personaId}/synthesize`, {
        method: "POST",
      });
      if (!resp.ok || !resp.body) {
        notification.error({
          message: t("comp.profile.toast.synthesisFailed"),
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
        notification.success({ message: t("comp.profile.toast.synthesisCompleted") });
      } else if (terminal === "profile.synthesis.degraded") {
        notification.warning({ message: t("comp.profile.toast.synthesisDegraded") });
      } else if (terminal === "profile.synthesis.failed") {
        notification.error({ message: t("comp.profile.toast.synthesisFailed") });
      } else if (terminal === "profile.synthesis.skipped") {
        notification.info({ message: t("comp.profile.toast.synthesisSkipped") });
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
        notification.success({ message: t("comp.profile.toast.manualSaved") });
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
        <Spin tip={t("comp.profile.loading")} />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("comp.profile.loadFailed")}
        description={String((error as Error).message ?? error)}
        style={{ margin: 16 }}
      />
    );
  }

  if (!data?.exists) {
    return (
      <Empty
        description={t("comp.profile.empty")}
        style={{ padding: 48 }}
      >
        <Button
          type="primary"
          icon={<ReloadOutlined spin={syncing} />}
          loading={syncing}
          onClick={handleSynthesize}
        >
          {t("comp.profile.synthesize")}
        </Button>
      </Empty>
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
          {t("comp.profile.synthesize")}
          {syncStatus
            ? ` (${syncStatus.replace("profile.synthesis.", "")})`
            : ""}
        </Button>
        <Select
          placeholder={t("comp.profile.history")}
          style={{ minWidth: 180 }}
          loading={historyLoading}
          onChange={handleHistoryChange}
          allowClear
          options={data.history.map((h) => ({ label: h.name, value: h.name }))}
        />
        <Button icon={<EditOutlined />} onClick={openManualEdit}>
          {t("comp.profile.editManual")}
        </Button>
      </Space>

      <Space wrap style={{ marginBottom: 16 }}>
        {fm && <Tag color="blue">v{fm.version}</Tag>}
        {fm?.data_window && <Tag>window {fm.data_window}</Tag>}
        {fm?.last_synthesized && (
          <Tag color="default">{t("comp.profile.synthesizedAt", { date: fm.last_synthesized.slice(0, 10) })}</Tag>
        )}
        {fm?.synthesizer_model && (
          <Tag color="purple">{fm.synthesizer_model}</Tag>
        )}
        {fm?.manually_edited && <Tag color="cyan">{t("comp.profile.userEdited")}</Tag>}
        {degraded && <Tag color="warning">{t("comp.profile.degraded")}</Tag>}
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
        title={t("comp.profile.manualEditTitle")}
        width={700}
        okText={t("button.save")}
        cancelText={t("common.cancel")}
        onOk={handleManualSave}
        onCancel={() => setManualOpen(false)}
        confirmLoading={manualSaving}
      >
        <Input.TextArea
          rows={16}
          value={manualDraft}
          onChange={(e) => setManualDraft(e.target.value)}
          placeholder={t("comp.profile.manualEditPlaceholder")}
        />
      </Modal>
    </div>
  );
}
