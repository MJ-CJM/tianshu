import { Button, Space, Tag, Typography, theme } from "antd";
import {
  ArrowRightOutlined,
  LoadingOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import GlowCard from "../common/GlowCard";
import SemanticTag from "../common/SemanticTag";
import { formatTime } from "../../utils/format";
import {
  deriveEdictPhase,
  PHASE_LABELS,
  PHASE_COLORS,
  type EdictPhase,
} from "../../utils/edictPhase";
import type { Edict, Memorial, PendingToolCall } from "../../api/types";
import styles from "../common/GlowCard.module.css";
import { useT, type TFunction } from "../../i18n";

interface EdictActivityCardProps {
  edict: Edict;
  latestMemorial: Memorial | null;
  pendingToolCalls?: PendingToolCall[];
  pendingDecisionCount?: number;
}

export default function EdictActivityCard({
  edict,
  latestMemorial,
  pendingToolCalls = [],
  pendingDecisionCount = 0,
}: EdictActivityCardProps) {
  const t = useT();
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const phase = deriveEdictPhase(latestMemorial);

  const handleClick = () => {
    navigate(`/edicts/${edict.id}`);
  };

  const hasPendingTool = pendingToolCalls.length > 0;
  const otherPendingDecisionCount = Math.max(
    0,
    pendingDecisionCount - pendingToolCalls.length,
  );
  const hasPendingDecision =
    pendingDecisionCount > 0 || phase === "needs_review";
  const borderColor =
    hasPendingDecision || hasPendingTool
      ? "var(--ts-color-warning)"
      : PHASE_COLORS[phase];

  return (
    <GlowCard
      hoverable
      className={phase === "running" ? styles.runningGlow : undefined}
      title={
        <Space>
          <span>{edict.title}</span>
          {edict.priority === "urgent" && (
            <SemanticTag colorVar="var(--ts-color-error)">
              {t("priority.urgent")}
            </SemanticTag>
          )}
          <SemanticTag
            colorVar={PHASE_COLORS[phase]}
            solid={phase === "needs_review"}
          >
            {PHASE_LABELS[phase]}
          </SemanticTag>
          {hasPendingTool && (
            <Tag color="orange">
              {t("comp.edictActivity.pendingTool", {
                n: pendingToolCalls.length,
              })}
            </Tag>
          )}
          {otherPendingDecisionCount > 0 && (
            <Tag color="orange">
              {t("comp.edictActivity.pendingDecision", {
                n: otherPendingDecisionCount,
              })}
            </Tag>
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
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            marginBottom: 12,
          }}
        >
          {pendingToolCalls.map((p) => (
            <Space key={p.decision_request_id} size={6} wrap>
              <Typography.Text code>{p.tool_name}</Typography.Text>
              {p.tool_tier ? <Tag color="orange">{p.tool_tier}</Tag> : null}
              {p.reason ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {p.reason}
                </Typography.Text>
              ) : null}
            </Space>
          ))}
        </div>
      )}
      <PhaseContent
        phase={phase}
        memorial={latestMemorial}
        token={token}
        t={t}
      />
      {(hasPendingDecision || hasPendingTool) && (
        <div
          style={{ marginTop: 12 }}
          onClick={(event) => event.stopPropagation()}
        >
          <Button
            size="small"
            icon={<ArrowRightOutlined />}
            onClick={handleClick}
          >
            {t("comp.edictActivity.openDecision")}
          </Button>
        </div>
      )}
      <div style={{ marginTop: 8 }}>
        <Typography.Text
          style={{ color: token.colorTextTertiary, fontSize: 12 }}
        >
          {t("comp.edictActivity.createdAt", {
            time: formatTime(edict.created_at),
          })}
        </Typography.Text>
      </div>
    </GlowCard>
  );
}

function PhaseContent({
  phase,
  memorial,
  token,
  t,
}: {
  phase: EdictPhase;
  memorial: Memorial | null;
  token: ReturnType<typeof theme.useToken>["token"];
  t: TFunction;
}) {
  if (phase === "no_memorial") {
    return (
      <div style={{ color: token.colorTextSecondary, fontSize: 13 }}>
        <ClockCircleOutlined style={{ marginRight: 6 }} />
        {t("comp.edictActivity.initializing")}
      </div>
    );
  }

  if (!memorial) return null;

  if (phase === "running") {
    return (
      <div style={{ color: token.colorTextSecondary, fontSize: 13 }}>
        <LoadingOutlined spin style={{ marginRight: 6 }} />
        {memorial.instruction ?? t("comp.edictActivity.executing")}
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
              {t("comp.edictActivity.auditReasons")}
            </Typography.Text>
            {memorial.audit.reasons.map((r, i) => (
              <div
                key={i}
                style={{
                  fontSize: 12,
                  color: token.colorWarning,
                  marginTop: 2,
                }}
              >
                {r}
              </div>
            ))}
          </div>
        )}
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
        <Typography.Text
          style={{ color: token.colorTextSecondary, fontSize: 13 }}
        >
          {memorial.instruction}
        </Typography.Text>
      )}
    </div>
  );
}
