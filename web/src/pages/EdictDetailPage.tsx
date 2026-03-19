import { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Button, Input, Modal, Spin, Typography, Tag, Space, Popconfirm, Collapse, Descriptions, message, theme } from "antd";
import { ArrowLeftOutlined, SendOutlined, CheckOutlined, ClockCircleOutlined, EditOutlined, StopOutlined, DeploymentUnitOutlined } from "@ant-design/icons";
import { useEdictDetail } from "../hooks/useEdictDetail";
import { followUpEdict, updateEdictStatus, updateEdict } from "../api/edicts";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import MonoText from "../components/common/MonoText";
import MemorialCard from "../components/memorial/MemorialCard";
import UsageDisplay from "../components/memorial/UsageDisplay";
import EventTimeline from "../components/memorial/EventTimeline";
import DecreeModal from "../components/decree/DecreeModal";
import { useDagByEdict } from "../hooks/useDag";
import { formatTime, truncateId } from "../utils/format";
import {
  EDICT_STATUS_LABELS,
  EDICT_STATUS_COLORS,
  PRIORITY_LABELS,
  PRIORITY_COLORS,
  REVIEW_POLICY_LABELS,
  SCHEDULE_TYPE_LABELS,
} from "../utils/constants";
import type { UsageSummary } from "../api/types";

export default function EdictDetailPage() {
  const { edictId } = useParams<{ edictId: string }>();
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const { edict, memorials, events, isLoading, refetch } = useEdictDetail(edictId ?? "");
  const [instruction, setInstruction] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editGoal, setEditGoal] = useState("");
  const [editContext, setEditContext] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const [decreeAction, setDecreeAction] = useState<string | null>(null);
  const [decreeModalOpen, setDecreeModalOpen] = useState(false);
  const { data: dagExecution } = useDagByEdict(edictId);
  const hasDag = dagExecution && dagExecution.nodes && dagExecution.nodes.length > 1;

  const hasActiveMemorial = memorials.some((m) =>
    ["running", "submitted", "scheduled", "planning", "auditing"].includes(m.status),
  );
  const pendingReviewMemorial = memorials.find((m) => m.review_status === "pending");
  const hasPendingReview = !!pendingReviewMemorial;
  const canFollowUp = edict?.status === "open" && !hasActiveMemorial && !hasPendingReview;

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
        <Space>
          {hasDag && (
            <Button
              icon={<DeploymentUnitOutlined />}
              onClick={() => navigate(`/dag/${dagExecution!.id}`)}
            >
              查看作战图
            </Button>
          )}
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate("/")}
          >
            返回总览
          </Button>
        </Space>
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
          <Typography.Text style={{ color: token.colorText, fontSize: 15 }}>
            {edict.goal}
          </Typography.Text>
        </div>
        {edict.context && (
          <div style={{ marginBottom: 12 }}>
            <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 13 }}>
              {edict.context}
            </Typography.Text>
          </div>
        )}
        <Descriptions
          size="small"
          column={4}
          style={{ marginBottom: 12 }}
          items={[
            {
              key: "priority",
              label: "优先级",
              children: (
                <Tag color={PRIORITY_COLORS[edict.priority] ?? "#1890ff"}>
                  {PRIORITY_LABELS[edict.priority] ?? edict.priority}
                </Tag>
              ),
            },
            {
              key: "review_policy",
              label: "审核策略",
              children: REVIEW_POLICY_LABELS[edict.review_policy] ?? edict.review_policy,
            },
            {
              key: "schedule",
              label: "调度方式",
              children: (
                <span>
                  {SCHEDULE_TYPE_LABELS[edict.schedule?.type] ?? edict.schedule?.type}
                  {edict.schedule?.cron && (
                    <MonoText style={{ marginLeft: 6, fontSize: 11, color: token.colorTextSecondary }}>
                      {edict.schedule.cron}
                    </MonoText>
                  )}
                  {edict.schedule?.at && (
                    <span style={{ marginLeft: 6, fontSize: 12, color: token.colorTextSecondary }}>
                      {formatTime(edict.schedule.at)}
                    </span>
                  )}
                </span>
              ),
            },
            {
              key: "source",
              label: "来源",
              children: edict.source,
            },
          ]}
        />

        {edict.constraints && edict.constraints.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12, marginRight: 8 }}>
              约束条件：
            </Typography.Text>
            {edict.constraints.map((c, i) => (
              <Tag key={i}>{c}</Tag>
            ))}
          </div>
        )}

        {edict.output_format && (
          <div style={{ marginBottom: 12 }}>
            <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
              输出格式：
            </Typography.Text>
            <pre style={{
              marginTop: 4,
              padding: 8,
              background: "var(--ts-color-bg-subtle)",
              borderRadius: 4,
              fontSize: 12,
              border: `1px solid ${token.colorBorder}`,
              whiteSpace: "pre-wrap",
            }}>
              {edict.output_format}
            </pre>
          </div>
        )}

        <Collapse
          ghost
          size="small"
          style={{ marginBottom: 12 }}
          items={[
            {
              key: "runtime",
              label: <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>执行参数</Typography.Text>,
              children: (
                <Descriptions
                  size="small"
                  column={3}
                  items={[
                    { key: "timeout", label: "超时", children: `${edict.runtime?.timeout_seconds ?? 300}s` },
                    { key: "iterations", label: "最大迭代", children: edict.runtime?.max_iterations ?? 20 },
                    { key: "concurrency", label: "并发", children: edict.runtime?.max_concurrency ?? 1 },
                    { key: "retry", label: "重试", children: edict.runtime?.retry_limit ?? 0 },
                    { key: "token_budget", label: "Token 预算", children: edict.runtime?.token_budget ?? "不限" },
                    { key: "cost_budget", label: "费用预算", children: edict.runtime?.cost_budget_cny != null ? `¥${edict.runtime.cost_budget_cny}` : "不限" },
                  ]}
                />
              ),
            },
          ]}
        />

        <Space size={16} style={{ color: token.colorTextSecondary, fontSize: 12 }}>
          <span title={edict.id}>
            <MonoText style={{ color: token.colorTextSecondary, fontSize: 11 }}>
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

      {hasPendingReview && edict.status === "open" && (
        <GlowCard title="批红" style={{ marginBottom: 24 }}>
          <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 13, display: "block", marginBottom: 16 }}>
            有奏折待批红，请选择操作：
          </Typography.Text>
          <Space wrap>
            {([
              { action: "approve", label: "准", type: "primary" as const },
              { action: "reject", label: "驳", type: "default" as const },
              { action: "retry", label: "重办", type: "default" as const },
              { action: "amend", label: "改批", type: "default" as const },
              { action: "cancel", label: "撤回", type: "default" as const, danger: true },
            ] as const).map((item) => (
              <Button
                key={item.action}
                type={item.type}
                danger={"danger" in item ? item.danger : false}
                onClick={() => {
                  setDecreeAction(item.action);
                  setDecreeModalOpen(true);
                }}
              >
                {item.label}
              </Button>
            ))}
          </Space>
          <DecreeModal
            memorial={pendingReviewMemorial ?? null}
            action={decreeAction}
            open={decreeModalOpen}
            onClose={() => {
              setDecreeModalOpen(false);
              setDecreeAction(null);
              refetch();
            }}
          />
        </GlowCard>
      )}

      {canFollowUp && (
        <GlowCard title="继续批示" style={{ marginBottom: 24 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "flex-end" }}>
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
              style={{ flex: 1 }}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleFollowUp}
              loading={submitting}
              disabled={!instruction.trim()}
            >
              继续批示 (Ctrl+Enter)
            </Button>
          </div>
        </GlowCard>
      )}

      {edict.status === "open" && (
        <div style={{ display: "flex", gap: 12, marginBottom: 24 }}>
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
        </div>
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
            <Typography.Text strong>敕令标题</Typography.Text>
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
