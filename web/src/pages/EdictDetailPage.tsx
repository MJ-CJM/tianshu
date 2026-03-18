import { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Button, Input, Spin, Typography, Descriptions, Tag, Space, Popconfirm, message } from "antd";
import { ArrowLeftOutlined, SendOutlined, CheckOutlined } from "@ant-design/icons";
import { useEdictDetail } from "../hooks/useEdictDetail";
import { followUpEdict, updateEdictStatus } from "../api/edicts";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import MonoText from "../components/common/MonoText";
import MemorialCard from "../components/memorial/MemorialCard";
import UsageDisplay from "../components/memorial/UsageDisplay";
import EventTimeline from "../components/memorial/EventTimeline";
import { formatTime } from "../utils/format";
import { EDICT_STATUS_LABELS, EDICT_STATUS_COLORS } from "../utils/constants";
import type { UsageSummary } from "../api/types";

export default function EdictDetailPage() {
  const { edictId } = useParams<{ edictId: string }>();
  const navigate = useNavigate();
  const { edict, memorials, events, isLoading, refetch } = useEdictDetail(edictId ?? "");
  const [instruction, setInstruction] = useState("");
  const [submitting, setSubmitting] = useState(false);

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
      <GlowCard title="敕令" style={{ marginBottom: 24 }}>
        <Descriptions column={2} size="small" colon={false}>
          <Descriptions.Item label="编号">
            <MonoText style={{ color: "#00d4ff", fontSize: 12 }}>
              {edict.id}
            </MonoText>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={EDICT_STATUS_COLORS[edict.status]}>
              {EDICT_STATUS_LABELS[edict.status]}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="颁发时间">
            {formatTime(edict.created_at)}
          </Descriptions.Item>
          <Descriptions.Item label="旨意" span={2}>
            {edict.goal}
          </Descriptions.Item>
          {edict.context && (
            <Descriptions.Item label="附则" span={2}>
              {edict.context}
            </Descriptions.Item>
          )}
        </Descriptions>
      </GlowCard>

      {memorials.map((memorial, index) => (
        <MemorialCard key={memorial.id} memorial={memorial} index={index} />
      ))}

      {hasUsage && <UsageDisplay usage={aggregatedUsage} />}

      {canFollowUp && (
        <GlowCard title="继续批阅" style={{ marginBottom: 24 }}>
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
              <Popconfirm
                title="确认结案？"
                description="结案后将无法继续下达指令"
                onConfirm={handleClose}
                okText="确认"
                cancelText="取消"
              >
                <Button icon={<CheckOutlined />} danger={false}>
                  结案
                </Button>
              </Popconfirm>
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={handleFollowUp}
                loading={submitting}
                disabled={!instruction.trim()}
              >
                继续批阅 (Ctrl+Enter)
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
    </PageContainer>
  );
}
