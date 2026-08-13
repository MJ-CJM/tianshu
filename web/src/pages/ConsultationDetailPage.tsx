import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Progress,
  Result,
  Space,
  Spin,
  Tag,
  Typography,
  theme,
} from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import { useConsultation, useConsultationLiveUpdates } from "../hooks/useConsultation";
import type { ConsultationResponse, PersonaOpinion } from "../api/types";
import { useT } from "../i18n";
import PageQueryError from "../components/states/PageQueryError";

const STANCE_COLORS: Record<string, string> = {
  support: "green",
  oppose: "red",
  conditional: "orange",
};

const STATUS_COLORS: Record<string, string> = {
  pending: "default",
  running: "processing",
  completed: "success",
  failed: "error",
};

const MARKDOWN_STYLE = {
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 13,
  lineHeight: 1.7,
} as const;

export default function ConsultationDetailPage() {
  const t = useT();
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const { consultationId } = useParams<{ consultationId: string }>();
  const activeId = consultationId ?? null;
  const query = useConsultation(activeId);
  const { data: consultation, isLoading } = query;
  useConsultationLiveUpdates(activeId);

  const back = (
    <Button size="small" onClick={() => navigate("/consultation")}>
      {t("consultation.back")}
    </Button>
  );

  if (query.error) {
    return (
      <PageContainer title={t("consultation.detailTitle")} extra={back}>
        <PageQueryError error={query.error} onRetry={() => void query.refetch()} />
      </PageContainer>
    );
  }

  if (isLoading && !consultation) {
    return (
      <PageContainer title={t("consultation.detailTitle")} extra={back}>
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }

  if (!consultation) {
    return (
      <PageContainer title={t("consultation.detailTitle")} extra={back}>
        <Empty description={t("consultation.notFound")} />
      </PageContainer>
    );
  }

  return (
    <PageContainer title={t("consultation.detailTitle")} extra={back}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Overview consultation={consultation} />
        <Progressing consultation={consultation} />
        {consultation.status === "failed" && (
          <Result
            status="error"
            title={t("consultation.failedTitle")}
            subTitle={consultation.error || t("consultation.failedSubtitle")}
          />
        )}
        {consultation.status === "completed" && consultation.error && (
          <Alert
            type="warning"
            showIcon
            message={t("consultation.partialFailure")}
            description={consultation.error}
          />
        )}
        {consultation.status === "completed" && consultation.opinions.length === 0 && (
          <GlowCard>
            <Empty description={t("consultation.emptyOpinions")} />
          </GlowCard>
        )}

        {consultation.opinions.map((opinion: PersonaOpinion) => (
          <GlowCard
            key={opinion.persona_id}
            title={
              <Space>
                <span>{opinion.persona_name}</span>
                <Tag>{opinion.department}</Tag>
                <Tag color={STANCE_COLORS[opinion.stance] ?? "default"}>
                  {t(`consultation.stance.${opinion.stance}`)}
                </Tag>
                {opinion.is_censor && <Tag color="volcano">{t("consultation.censor")}</Tag>}
              </Space>
            }
          >
            <div className="memorial-markdown" style={{ color: token.colorText, ...MARKDOWN_STYLE }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{opinion.opinion}</ReactMarkdown>
            </div>
            {opinion.conditions.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {t("consultation.conditions")}
                </Typography.Text>
                <ul style={{ margin: "4px 0 0", paddingLeft: 20 }}>
                  {opinion.conditions.map((c, i) => (
                    <li key={i} style={{ fontSize: 13, color: token.colorText }}>
                      {c}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {opinion.key_points.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {opinion.key_points.map((kp, i) => (
                  <Tag key={i} style={{ marginBottom: 4 }}>
                    {kp}
                  </Tag>
                ))}
              </div>
            )}
          </GlowCard>
        ))}

        {consultation.synthesis && (
          <GlowCard
            title={
              <Space>
                <span>{t("consultation.synthesisTitle")}</span>
                <Tag color="blue">{synthesizerLabel(consultation, t)}</Tag>
              </Space>
            }
          >
            <div className="memorial-markdown" style={{ color: token.colorText, ...MARKDOWN_STYLE }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{consultation.synthesis}</ReactMarkdown>
            </div>
          </GlowCard>
        )}

        {consultation.decision && (
          <GlowCard
            title={
              <Space>
                <span>{t("consultation.decisionTitle")}</span>
                <Tag color="blue">{synthesizerLabel(consultation, t)}</Tag>
              </Space>
            }
            style={{ borderLeft: `3px solid ${token.colorSuccess}` }}
          >
            <div className="memorial-markdown" style={{ color: token.colorText, ...MARKDOWN_STYLE }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{consultation.decision}</ReactMarkdown>
            </div>
          </GlowCard>
        )}
      </Space>
    </PageContainer>
  );
}

/** 综合/决策的署名：指定了汇聚官就报其名，否则是通用「首席顾问」。 */
function synthesizerLabel(
  consultation: ConsultationResponse,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  if (!consultation.synthesizer_name) return t("consultation.synthesizerDefault");
  return consultation.synthesizer_department
    ? `${consultation.synthesizer_name} (${consultation.synthesizer_department})`
    : consultation.synthesizer_name;
}

function Overview({ consultation }: { consultation: ConsultationResponse }) {
  const t = useT();
  return (
    <GlowCard>
      <Descriptions size="small" column={1}>
        <Descriptions.Item label={t("consultation.topic")}>
          {consultation.request?.topic ?? "-"}
        </Descriptions.Item>
        {consultation.request?.context && (
          <Descriptions.Item label={t("consultation.context")}>
            {consultation.request.context}
          </Descriptions.Item>
        )}
        <Descriptions.Item label={t("consultation.statusLabel")}>
          <Tag color={STATUS_COLORS[consultation.status] ?? "default"}>
            {t(`consultation.status.${consultation.status}`)}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label={t("consultation.startedAt")}>
          {new Date(consultation.created_at).toLocaleString()}
        </Descriptions.Item>
        {consultation.completed_at && (
          <Descriptions.Item label={t("consultation.finishedAt")}>
            {new Date(consultation.completed_at).toLocaleString()}
          </Descriptions.Item>
        )}
      </Descriptions>
    </GlowCard>
  );
}

function Progressing({ consultation }: { consultation: ConsultationResponse }) {
  const t = useT();
  if (consultation.status !== "pending" && consultation.status !== "running") return null;

  const total = consultation.request?.persona_ids?.length ?? 0;
  const done = consultation.opinions.length;
  // 意见收齐后还在 running，说明正在做综合——不该继续显示停在 100% 的「进行中」
  const synthesizing = total > 0 && done >= total;

  return (
    <GlowCard>
      <div style={{ textAlign: "center", padding: 24 }}>
        <Spin />
        <Typography.Text style={{ display: "block", marginTop: 12 }}>
          {consultation.status === "pending"
            ? t("consultation.preparing")
            : synthesizing
              ? t("consultation.synthesizing")
              : t("consultation.running")}
        </Typography.Text>
        {total > 0 && !synthesizing && (
          <div style={{ maxWidth: 320, margin: "16px auto 0" }}>
            <Progress percent={Math.round((done / total) * 100)} size="small" status="active" />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("consultation.progress", { done, total })}
            </Typography.Text>
          </div>
        )}
      </div>
    </GlowCard>
  );
}
