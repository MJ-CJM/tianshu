import { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Button, Input, Modal, Spin, Typography, Tag, Space, Popconfirm, message } from "antd";
import { ArrowLeftOutlined, SendOutlined, CheckOutlined, ClockCircleOutlined, EditOutlined, StopOutlined } from "@ant-design/icons";
import { useEdictDetail } from "../hooks/useEdictDetail";
import { followUpEdict, updateEdictStatus, updateEdict } from "../api/edicts";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import MonoText from "../components/common/MonoText";
import MemorialCard from "../components/memorial/MemorialCard";
import UsageDisplay from "../components/memorial/UsageDisplay";
import EventTimeline from "../components/memorial/EventTimeline";
import { formatTime, truncateId } from "../utils/format";
import { EDICT_STATUS_LABELS, EDICT_STATUS_COLORS } from "../utils/constants";
import type { UsageSummary } from "../api/types";

export default function EdictDetailPage() {
  const { edictId } = useParams<{ edictId: string }>();
  const navigate = useNavigate();
  const { edict, memorials, events, isLoading, refetch } = useEdictDetail(edictId ?? "");
  const [instruction, setInstruction] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editGoal, setEditGoal] = useState("");
  const [editContext, setEditContext] = useState("");
  const [editSaving, setEditSaving] = useState(false);

  const hasActiveMemorial = memorials.some(
    (m) => m.status === "running" || m.status === "submitted",
  );
  const canFollowUp = edict?.status === "open" && !hasActiveMemorial;
  const canClose = edict?.status === "open" && !hasActiveMemorial;

  const aggregatedUsage = useMemo<UsageSummary>(() => {
    return memorials.reduce(
      (acc, m) => ({
        prompt_tokens: acc.prompt_tokens + (m.usage?.prompt_tokens ?? 0),
        completion_tokens: acc.completion_tokens + (m.usage?.completion_tokens ?? 0),
        total_tokens: acc.total_tokens + (m.usage?.total_tokens ?? 0),
      }),
      { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    );
  }, [memorials]);

  const hasUsage = aggregatedUsage.total_tokens > 0;

  const handleFollowUp = async () => {
    if (!edictId || !instruction.trim()) return;
    setSubmitting(true);
    try {
      await followUpEdict(edictId, { instruction: instruction.trim() });
      setInstruction("");
      refetch();
    } catch {
      message.error("提交后续指令失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleClose = async () => {
    if (!edictId) return;
    try {
      await updateEdictStatus(edictId, "completed");
      refetch();
      message.success("敕令已结案");
    } catch {
      message.error("结案失败");
    }
  };

  const handleCancel = async () => {
    if (!edictId) return;
    try {
      await updateEdictStatus(edictId, "cancelled");
      refetch();
      message.success("敕令已废除");
    } catch {
      message.error("废除失败");
    }
  };

  const openEditModal = () => {
    if (!edict) return;
    setEditTitle(edict.title);
    setEditGoal(edict.goal);
    setEditContext(edict.context ?? "");
    setEditOpen(true);
  };

  const handleEditSave = async () => {
    if (!edictId) return;
    setEditSaving(true);
    try {
      await updateEdict(edictId, {
        title: editTitle.trim() || undefined,
        goal: editGoal.trim() || undefined,
        context: editContext.trim() || undefined,
      });
      setEditOpen(false);
      refetch();
      message.success("敕令已更新");
    } catch {
      message.error("更新失败");
    } finally {
      setEditSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "50vh",
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  if (!edict) {
    return (
      <PageContainer title="敕令详情">
        <Typography.Text type="secondary">未见此敕令</Typography.Text>
      </PageContainer>
    );
  }

  return (
    <PageContainer
      title="敕令详情"
      extra={
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate("/")}
        >
          返回卷宗
        </Button>
      }
    >
      <GlowCard
        title={
          <Space size="middle">
            <span>{edict.title || "敕令"}</span>
            <Tag color={EDICT_STATUS_COLORS[edict.status]}>
              {EDICT_STATUS_LABELS[edict.status]}
            </Tag>
            {edict.status === "open" && (
              <Button
                type="text"
                size="small"
                icon={<EditOutlined />}
                onClick={openEditModal}
              />
            )}
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <div style={{ marginBottom: 12 }}>
          <Typography.Text style={{ color: "#e2e8f0", fontSize: 15 }}>
            {edict.goal}
          </Typography.Text>
        </div>
        {edict.context && (
          <div style={{ marginBottom: 12 }}>
            <Typography.Text style={{ color: "#94a3b8", fontSize: 13 }}>
              {edict.context}
            </Typography.Text>
          </div>
        )}
        <Space size={16} style={{ color: "#475569", fontSize: 12 }}>
          <span title={edict.id}>
            <MonoText style={{ color: "#475569", fontSize: 11 }}>
              {truncateId(edict.id, 12)}
            </MonoText>
          </span>
          <span>
            <ClockCircleOutlined style={{ marginRight: 4 }} />
            {formatTime(edict.created_at)}
          </span>
        </Space>
      </GlowCard>

      {memorials.map((memorial, index) => (
        <MemorialCard key={memorial.id} memorial={memorial} index={index} />
      ))}

      {hasUsage && <UsageDisplay usage={aggregatedUsage} />}

      {canFollowUp && (
        <GlowCard title="继续批示" style={{ marginBottom: 24 }}>
          <Space direction="vertical" style={{ width: "100%" }} size="middle">
            <Input.TextArea
              placeholder="输入后续指令..."
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              onPressEnter={(e) => {
                if (e.ctrlKey || e.metaKey) handleFollowUp();
              }}
              disabled={submitting}
              rows={2}
              autoSize={{ minRows: 2, maxRows: 6 }}
            />
            <Space style={{ justifyContent: "space-between", width: "100%", display: "flex" }}>
              <Space>
                <Popconfirm
                  title="确认结案？"
                  description="结案后将无法继续下达指令"
                  onConfirm={handleClose}
                  okText="确认"
                  cancelText="取消"
                >
                  <Button icon={<CheckOutlined />}>
                    结案
                  </Button>
                </Popconfirm>
                <Popconfirm
                  title="确认废除？"
                  description="废除后敕令将被标记为已撤回"
                  onConfirm={handleCancel}
                  okText="确认"
                  cancelText="取消"
                >
                  <Button icon={<StopOutlined />} danger>
                    废除
                  </Button>
                </Popconfirm>
              </Space>
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={handleFollowUp}
                loading={submitting}
                disabled={!instruction.trim()}
              >
                继续批示 (Ctrl+Enter)
              </Button>
            </Space>
          </Space>
        </GlowCard>
      )}

      {!canFollowUp && canClose && (
        <GlowCard style={{ marginBottom: 24 }}>
          <div style={{ textAlign: "right" }}>
            <Popconfirm
              title="确认结案？"
              description="结案后将无法继续下达指令"
              onConfirm={handleClose}
              okText="确认"
              cancelText="取消"
            >
              <Button icon={<CheckOutlined />}>结案</Button>
            </Popconfirm>
          </div>
        </GlowCard>
      )}

      {events.length > 0 && <EventTimeline events={events} />}

      <Modal
        title="编辑敕令"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={handleEditSave}
        confirmLoading={editSaving}
        okText="保存"
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <div>
            <Typography.Text strong>卷宗名</Typography.Text>
            <Input
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              style={{ marginTop: 8 }}
            />
          </div>
          <div>
            <Typography.Text strong>旨意</Typography.Text>
            <Input.TextArea
              value={editGoal}
              onChange={(e) => setEditGoal(e.target.value)}
              rows={3}
              autoSize={{ minRows: 2, maxRows: 6 }}
              style={{ marginTop: 8 }}
            />
          </div>
          <div>
            <Typography.Text strong>附则</Typography.Text>
            <Input.TextArea
              value={editContext}
              onChange={(e) => setEditContext(e.target.value)}
              rows={3}
              autoSize={{ minRows: 2, maxRows: 6 }}
              placeholder="可选"
              style={{ marginTop: 8 }}
            />
          </div>
        </Space>
      </Modal>
    </PageContainer>
  );
}
