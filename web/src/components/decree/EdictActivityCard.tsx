import { Button, Space, Tag, Typography, theme } from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  RedoOutlined,
  EditOutlined,
  StopOutlined,
  LoadingOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import GlowCard from "../common/GlowCard";
import PendingToolCallCard from "./PendingToolCallCard";
import { formatTime } from "../../utils/format";
import {
  deriveEdictPhase,
  PHASE_LABELS,
  PHASE_COLORS,
  type EdictPhase,
} from "../../utils/edictPhase";
import type { Edict, Memorial, PendingToolCall } from "../../api/types";
import styles from "../common/GlowCard.module.css";

interface EdictActivityCardProps {
  edict: Edict;
  latestMemorial: Memorial | null;
  onOpenDecree: (memorial: Memorial, action: string) => void;
  pendingToolCalls?: PendingToolCall[];
}

export default function EdictActivityCard({
  edict,
  latestMemorial,
  onOpenDecree,
  pendingToolCalls = [],
}: EdictActivityCardProps) {
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const phase = deriveEdictPhase(latestMemorial);

  const handleClick = () => {
    navigate(`/edicts/${edict.id}`);
  };

  const hasPendingTool = pendingToolCalls.length > 0;
  const borderColor = hasPendingTool ? "#faad14" : PHASE_COLORS[phase];

  return (
    <GlowCard
      hoverable
      className={phase === "running" ? styles.runningGlow : undefined}
      title={
        <Space>
          <span>{edict.title}</span>
          {edict.priority === "urgent" && (
            <Tag color="#ff4d4f">紧急</Tag>
          )}
          <Tag color={PHASE_COLORS[phase]}>{PHASE_LABELS[phase]}</Tag>
          {hasPendingTool && (
            <Tag color="orange">工具待批 ({pendingToolCalls.length})</Tag>
          )}
          <Typography.Text
            style={{ color: token.colorTextSecondary, fontSize: 12 }}
          >
            {edict.id.slice(0, 12)}…
          </Typography.Text>
        </Space>
      }
      style={{
        borderLeft: `3px solid ${borderColor}`,
        cursor: "pointer",
      }}
      onClick={handleClick}
    >
      {hasPendingTool && (
        <div onClick={(e) => e.stopPropagation()}>
          {pendingToolCalls.map((p) => (
            <PendingToolCallCard key={p.memorial_id} pending={p} />
          ))}
        </div>
      )}
      <PhaseContent
        phase={phase}
        memorial={latestMemorial}
        token={token}
        onOpenDecree={onOpenDecree}
      />
      <div style={{ marginTop: 8 }}>
        <Typography.Text
          style={{ color: token.colorTextTertiary, fontSize: 12 }}
        >
          创建于 {formatTime(edict.created_at)}
        </Typography.Text>
      </div>
    </GlowCard>
  );
}

function PhaseContent({
  phase,
  memorial,
  token,
  onOpenDecree,
}: {
  phase: EdictPhase;
  memorial: Memorial | null;
  token: ReturnType<typeof theme.useToken>["token"];
  onOpenDecree: (memorial: Memorial, action: string) => void;
}) {
  if (phase === "no_memorial") {
    return (
      <div style={{ color: token.colorTextSecondary, fontSize: 13 }}>
        <ClockCircleOutlined style={{ marginRight: 6 }} />
        正在初始化…
      </div>
    );
  }

  if (!memorial) return null;

  if (phase === "running") {
    return (
      <div style={{ color: token.colorTextSecondary, fontSize: 13 }}>
        <LoadingOutlined spin style={{ marginRight: 6 }} />
        {memorial.instruction ?? "执行中…"}
      </div>
    );
  }

  if (phase === "needs_review") {
    return (
      <>
        {memorial.instruction && (
          <div style={{ marginBottom: 8 }}>
            <Typography.Text style={{ color: token.colorText, fontSize: 13 }}>
              {memorial.instruction}
            </Typography.Text>
          </div>
        )}
        {memorial.audit && memorial.audit.reasons.length > 0 && (
          <div
            style={{
              marginBottom: 8,
              padding: 8,
              background: "var(--ts-color-bg-subtle)",
              borderRadius: 4,
            }}
          >
            <Typography.Text
              style={{ fontSize: 12, color: token.colorTextSecondary }}
            >
              审计原因：
            </Typography.Text>
            {memorial.audit.reasons.map((r, i) => (
              <div
                key={i}
                style={{ fontSize: 12, color: token.colorWarning, marginTop: 2 }}
              >
                {r}
              </div>
            ))}
          </div>
        )}
        <Space wrap onClick={(e) => e.stopPropagation()}>
          <Button
            type="primary"
            size="small"
            icon={<CheckCircleOutlined />}
            onClick={() => onOpenDecree(memorial, "approve")}
          >
            准
          </Button>
          <Button
            danger
            size="small"
            icon={<CloseCircleOutlined />}
            onClick={() => onOpenDecree(memorial, "reject")}
          >
            驳
          </Button>
          <Button
            size="small"
            icon={<RedoOutlined />}
            onClick={() => onOpenDecree(memorial, "retry")}
          >
            重办
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => onOpenDecree(memorial, "amend")}
          >
            改批
          </Button>
          <Button
            size="small"
            icon={<StopOutlined />}
            onClick={() => onOpenDecree(memorial, "cancel")}
          >
            撤回
          </Button>
        </Space>
      </>
    );
  }

  // idle
  return (
    <div>
      {memorial.summary && (
        <Typography.Paragraph
          style={{ color: token.colorText, fontSize: 13, marginBottom: 0 }}
          ellipsis={{ rows: 2 }}
        >
          {memorial.summary}
        </Typography.Paragraph>
      )}
      {!memorial.summary && memorial.instruction && (
        <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 13 }}>
          {memorial.instruction}
        </Typography.Text>
      )}
    </div>
  );
}
