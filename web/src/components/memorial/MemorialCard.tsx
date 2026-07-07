import { Space, Typography, Tag, Button, theme } from "antd";
import { SyncOutlined, ClockCircleOutlined, SafetyCertificateOutlined, PaperClipOutlined, LinkOutlined, UserOutlined, DeploymentUnitOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useNavigate } from "react-router-dom";
import type { Memorial } from "../../api/types";
import { usePersonas } from "../../hooks/usePersonas";
import GlowCard from "../common/GlowCard";
import StatusTag from "../edict/StatusTag";
import { formatDuration, formatTime } from "../../utils/format";
import { parseErrorMessage } from "../../utils/errorMessage";
import { STATUS_COLORS } from "../../utils/constants";
import { useT } from "../../i18n";
import glowStyles from "../common/GlowCard.module.css";

const AUDIT_COLORS: Record<string, string> = {
  pass: "success",
  flag: "warning",
  block: "error",
};

interface MemorialCardProps {
  memorial: Memorial;
  index?: number;
}

export default function MemorialCard({ memorial, index }: MemorialCardProps) {
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const { data: personas } = usePersonas();
  const t = useT();
  const memorialTitle = t("memorial.title");
  const attemptLabel = memorial.attempt > 1 ? ` ${t("memorial.attemptLabel", { n: memorial.attempt })}` : "";
  const title = index !== undefined ? `${memorialTitle} #${index + 1}${attemptLabel}` : `${memorialTitle}${attemptLabel}`;
  const isRunning = memorial.status === "running";
  const borderColor = STATUS_COLORS[memorial.status] ?? token.colorBorder;
  const duration = formatDuration(memorial.started_at, memorial.completed_at);
  const hasDuration = duration !== "—";

  const showSummary = memorial.summary && memorial.summary !== memorial.result;

  return (
    <GlowCard
      title={
        <Space size="middle">
          <span>{title}</span>
          <StatusTag status={memorial.status} />
          {isRunning && <SyncOutlined spin style={{ color: token.colorInfo }} />}
          {memorial.audit && (
            <Tag
              icon={<SafetyCertificateOutlined />}
              color={AUDIT_COLORS[memorial.audit.verdict] ?? "default"}
            >
              {t(`audit.label.${memorial.audit.verdict}`)}
            </Tag>
          )}
          {memorial.review_status === "pending" && (
            <Button
              type="link"
              size="small"
              onClick={() => navigate(`/edicts/${memorial.edict_id}`)}
              style={{ padding: 0 }}
            >
              {t("memorial.review.pending")}
            </Button>
          )}
          <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
            <ClockCircleOutlined style={{ marginRight: 4 }} />
            {formatTime(memorial.started_at ?? memorial.created_at)}
            {hasDuration && ` · ${duration}`}
          </Typography.Text>
          {memorial.persona_id && (() => {
            const persona = (personas ?? []).find((p) => p.id === memorial.persona_id);
            return (
              <Tag style={{ fontSize: 11 }}>
                <UserOutlined style={{ marginRight: 2 }} />
                {persona ? `${persona.name}（${persona.department}）` : memorial.persona_id}
              </Tag>
            );
          })()}
          {memorial.dag_node_id && (
            <Tag style={{ fontSize: 11 }} color="blue">
              <DeploymentUnitOutlined style={{ marginRight: 2 }} />
              {memorial.dag_node_id}
            </Tag>
          )}
        </Space>
      }
      className={isRunning ? glowStyles.runningGlow : undefined}
      style={{
        marginBottom: 24,
        borderLeft: `3px solid ${borderColor}`,
      }}
    >
      {memorial.instruction && (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
            {t("memorial.field.instruction")}：
          </Typography.Text>
          <Typography.Text style={{ color: token.colorText }}>
            {memorial.instruction}
          </Typography.Text>
        </div>
      )}

      {showSummary && (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("memorial.field.summary")}
          </Typography.Text>
          <Typography.Paragraph
            style={{ color: token.colorText, marginTop: 4, marginBottom: 0 }}
          >
            {memorial.summary}
          </Typography.Paragraph>
        </div>
      )}

      {memorial.result && (
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {showSummary ? t("memorial.field.detail") : t("memorial.field.report")}
          </Typography.Text>
          <div
            className="memorial-markdown"
            style={{
              color: token.colorText,
              marginTop: 4,
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 13,
              background: "var(--ts-color-bg-subtle)",
              padding: 12,
              borderRadius: 6,
              border: `1px solid ${token.colorBorder}`,
              lineHeight: 1.7,
            }}
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {memorial.result}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {memorial.error && (() => {
        const parsed = parseErrorMessage(memorial.error, t);
        return (
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="danger" style={{ fontSize: 12 }}>
              {t("memorial.field.error")}
            </Typography.Text>
            {parsed && parsed.headline !== parsed.raw ? (
              <>
                <Typography.Paragraph
                  style={{
                    color: token.colorError,
                    marginTop: 4,
                    marginBottom: 0,
                    fontSize: 14,
                    fontWeight: 500,
                  }}
                >
                  {parsed.headline}
                </Typography.Paragraph>
                <Typography.Text
                  style={{
                    color: token.colorTextSecondary,
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 11,
                  }}
                >
                  {parsed.raw}
                </Typography.Text>
              </>
            ) : (
              <Typography.Paragraph
                style={{
                  color: token.colorError,
                  marginTop: 4,
                  marginBottom: 0,
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: 13,
                }}
              >
                {memorial.error}
              </Typography.Paragraph>
            )}
          </div>
        );
      })()}

      {memorial.artifacts && memorial.artifacts.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            <PaperClipOutlined style={{ marginRight: 4 }} />
            {t("memorial.field.artifacts")}
          </Typography.Text>
          <div style={{ marginTop: 4, display: "flex", flexWrap: "wrap", gap: 8 }}>
            {memorial.artifacts.map((artifact, i) => (
              <Tag key={i} icon={<LinkOutlined />}>
                {artifact.url ? (
                  <a href={artifact.url} target="_blank" rel="noopener noreferrer">
                    {artifact.name}
                  </a>
                ) : (
                  artifact.name
                )}
                <span style={{ marginLeft: 4, color: token.colorTextTertiary, fontSize: 11 }}>
                  ({artifact.type})
                </span>
              </Tag>
            ))}
          </div>
        </div>
      )}
    </GlowCard>
  );
}
