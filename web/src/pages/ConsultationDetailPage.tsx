import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Divider,
  Empty,
  Input,
  Progress,
  Result,
  Select,
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
import {
  useAppendRound,
  useConsultation,
  useConsultationLiveUpdates,
  useSetVerdict,
  useSynthesizeRound,
} from "../hooks/useConsultation";
import { usePersonas } from "../hooks/usePersonas";
import type {
  ConsultationResponse,
  ConsultationRound,
  PersonaOpinion,
  ToolTrace,
} from "../api/types";
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

type TFunc = (key: string, vars?: Record<string, string | number>) => string;

export default function ConsultationDetailPage() {
  const t = useT();
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

  const busy = consultation.rounds.some(
    (r) => r.status === "pending" || r.status === "running",
  );

  return (
    <PageContainer title={t("consultation.detailTitle")} extra={back}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Overview consultation={consultation} />

        {consultation.rounds.map((round) => (
          <RoundBlock
            key={round.id}
            round={round}
            roster={consultation.request?.persona_ids ?? []}
          />
        ))}

        <VerdictPanel consultation={consultation} />
        <FollowUpPanel consultation={consultation} disabled={busy} />
      </Space>
    </PageContainer>
  );
}

function Overview({ consultation }: { consultation: ConsultationResponse }) {
  const t = useT();
  const request = consultation.request;
  return (
    <GlowCard>
      <Descriptions size="small" column={1}>
        <Descriptions.Item label={t("consultation.topic")}>
          {request?.topic ?? "-"}
        </Descriptions.Item>
        {request?.context && (
          <Descriptions.Item label={t("consultation.context")}>
            {request.context}
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
      </Descriptions>
    </GlowCard>
  );
}

function RoundBlock({ round, roster }: { round: ConsultationRound; roster: string[] }) {
  const t = useT();
  const { token } = theme.useToken();
  const synthesize = useSynthesizeRound(round.consultation_id);
  const personasQuery = usePersonas();
  // 首轮自动票拟；后续轮次由用户看完意见后自行决定要不要汇总
  const canSynthesize =
    round.status === "completed" && round.opinions.length > 0 && !round.synthesis;

  // 点名了谁：靠「谁回答了」反推不可靠——某位官员失败时就再也看不出点过他
  const named = round.participant_ids;
  const isEveryone =
    named.length === roster.length && roster.every((id) => named.includes(id));
  const nameOf = (id: string) => {
    const persona = (personasQuery.data ?? []).find((p) => p.id === id);
    return persona ? persona.name : id;
  };

  return (
    <div>
      <Divider orientation="left" style={{ marginTop: 0 }}>
        <Space>
          <Typography.Text strong>
            {t("consultation.roundLabel", { n: round.round_index + 1 })}
          </Typography.Text>
          <Tag color={STATUS_COLORS[round.status] ?? "default"}>
            {t(`consultation.status.${round.status}`)}
          </Tag>
        </Space>
      </Divider>

      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {round.round_index === 0
            ? t("consultation.topic")
            : t("consultation.followUpLabel")}
          ：{round.prompt}
        </Typography.Text>

        <Space wrap size={4}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("consultation.askedLabel")}
          </Typography.Text>
          {isEveryone || named.length === 0 ? (
            <Tag>{t("consultation.askedEveryone")}</Tag>
          ) : (
            named.map((id) => (
              <Tag key={id} color="geekblue">
                @{nameOf(id)}
              </Tag>
            ))
          )}
        </Space>

        <RoundProgress round={round} />

        {round.status === "failed" && (
          <Result
            status="error"
            title={t("consultation.failedTitle")}
            subTitle={round.error || t("consultation.failedSubtitle")}
          />
        )}
        {round.status === "completed" && round.error && (
          <Alert
            type="warning"
            showIcon
            message={t("consultation.partialFailure")}
            description={round.error}
          />
        )}
        {round.status === "completed" && round.opinions.length === 0 && (
          <GlowCard>
            <Empty description={t("consultation.emptyOpinions")} />
          </GlowCard>
        )}

        {round.opinions.map((opinion: PersonaOpinion) => (
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
                {opinion.tool_calls?.length > 0 && (
                  <Tag color="cyan">
                    {t("consultation.verified", { n: opinion.tool_calls.length })}
                  </Tag>
                )}
              </Space>
            }
          >
            <div className="memorial-markdown" style={{ color: token.colorText, ...MARKDOWN_STYLE }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{opinion.opinion}</ReactMarkdown>
            </div>
            <ToolTraceList traces={opinion.tool_calls ?? []} />
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
          </GlowCard>
        ))}

        {canSynthesize && (
          <GlowCard>
            <Space direction="vertical" size="small" style={{ width: "100%" }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("consultation.synthesizeHint")}
              </Typography.Text>
              <Button
                loading={synthesize.isPending}
                onClick={() => synthesize.mutate(round.id)}
              >
                {t("consultation.synthesizeAction")}
              </Button>
              {synthesize.error && <PageQueryError error={synthesize.error} />}
            </Space>
          </GlowCard>
        )}

        {round.synthesis && (
          <GlowCard
            title={
              <Space>
                <span>{t("consultation.synthesisTitle")}</span>
                <Tag color="blue">{synthesizerLabel(round, t)}</Tag>
              </Space>
            }
          >
            <div className="memorial-markdown" style={{ color: token.colorText, ...MARKDOWN_STYLE }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{round.synthesis}</ReactMarkdown>
            </div>
          </GlowCard>
        )}

        {round.proposal && (
          <GlowCard
            title={
              <Space>
                <span>{t("consultation.proposalTitle")}</span>
                <Tag color="blue">{synthesizerLabel(round, t)}</Tag>
              </Space>
            }
          >
            <div className="memorial-markdown" style={{ color: token.colorText, ...MARKDOWN_STYLE }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{round.proposal}</ReactMarkdown>
            </div>
          </GlowCard>
        )}
      </Space>
    </div>
  );
}

/** 查证痕迹：没有它，读者无从判断这段意见是查过的还是凭旧记忆编的（issue #59）。 */
function ToolTraceList({ traces }: { traces: ToolTrace[] }) {
  const t = useT();
  const { token } = theme.useToken();
  if (traces.length === 0) return null;

  return (
    <Collapse
      ghost
      size="small"
      style={{ marginTop: 8 }}
      items={[
        {
          key: "traces",
          label: (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("consultation.traceTitle", { n: traces.length })}
            </Typography.Text>
          ),
          children: (
            <Space direction="vertical" size={4} style={{ width: "100%" }}>
              {traces.map((trace, i) => (
                <div key={i} style={{ fontSize: 12, fontFamily: "'JetBrains Mono', monospace" }}>
                  <Space size={4} wrap>
                    <Tag color={trace.is_error ? "red" : "cyan"}>{trace.tool}</Tag>
                    <Typography.Text type="secondary">{trace.args_preview}</Typography.Text>
                  </Space>
                  <div style={{ color: token.colorTextSecondary, paddingLeft: 8, marginTop: 2 }}>
                    → {trace.result_preview}
                  </div>
                </div>
              ))}
            </Space>
          ),
        },
      ]}
    />
  );
}

function RoundProgress({ round }: { round: ConsultationRound }) {
  const t = useT();
  if (round.status !== "pending" && round.status !== "running") return null;

  const total = round.participant_ids.length;
  const done = round.opinions.length;
  // 意见收齐后还在 running，说明正在票拟——不该继续显示停在 100% 的「进行中」
  const synthesizing = total > 0 && done >= total;

  return (
    <GlowCard>
      <div style={{ textAlign: "center", padding: 24 }}>
        <Spin />
        <Typography.Text style={{ display: "block", marginTop: 12 }}>
          {round.status === "pending"
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

/** 裁决：LLM 只出票拟，最终决定由用户写下（issue #55）。 */
function VerdictPanel({ consultation }: { consultation: ConsultationResponse }) {
  const t = useT();
  const { token } = theme.useToken();
  const [draft, setDraft] = useState("");
  const mutation = useSetVerdict(consultation.id);

  if (consultation.verdict) {
    return (
      <GlowCard
        title={
          <Space>
            <span>{t("consultation.verdictTitle")}</span>
            {consultation.verdict_at && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {new Date(consultation.verdict_at).toLocaleString()}
              </Typography.Text>
            )}
          </Space>
        }
        style={{ borderLeft: `3px solid ${token.colorSuccess}` }}
      >
        <div className="memorial-markdown" style={{ color: token.colorText, ...MARKDOWN_STYLE }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{consultation.verdict}</ReactMarkdown>
        </div>
      </GlowCard>
    );
  }

  return (
    <GlowCard
      title={
        <Space>
          <span>{t("consultation.verdictTitle")}</span>
          <Tag>{t("consultation.verdictPending")}</Tag>
        </Space>
      }
      style={{ borderLeft: `3px solid ${token.colorWarning}` }}
    >
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Input.TextArea
          rows={3}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t("consultation.verdictPlaceholder")}
        />
        <Button
          type="primary"
          loading={mutation.isPending}
          disabled={!draft.trim()}
          onClick={() => mutation.mutate(draft.trim(), { onSuccess: () => setDraft("") })}
        >
          {t("consultation.verdictSubmit")}
        </Button>
        {mutation.error && <PageQueryError error={mutation.error} />}
      </Space>
    </GlowCard>
  );
}

/** 追问：@指定官员则仅其作答，不指定则沿用首轮全体（issue #55）。 */
function FollowUpPanel({
  consultation,
  disabled,
}: {
  consultation: ConsultationResponse;
  disabled: boolean;
}) {
  const t = useT();
  const [prompt, setPrompt] = useState("");
  const [mentions, setMentions] = useState<string[]>([]);
  const mutation = useAppendRound(consultation.id);
  const personasQuery = usePersonas();

  const roster = consultation.request?.persona_ids ?? [];
  const options = (personasQuery.data ?? [])
    .filter((p) => roster.includes(p.id))
    .map((p) => ({ value: p.id, label: `${p.name} (${p.department})` }));

  const submit = () => {
    if (!prompt.trim()) return;
    mutation.mutate(
      { prompt: prompt.trim(), participant_ids: mentions },
      {
        onSuccess: () => {
          setPrompt("");
          setMentions([]);
        },
      },
    );
  };

  return (
    <GlowCard title={t("consultation.followUpTitle")}>
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Select
          mode="multiple"
          allowClear
          style={{ width: "100%" }}
          placeholder={t("consultation.mentionPlaceholder")}
          value={mentions}
          onChange={setMentions}
          options={options}
        />
        <Input.TextArea
          rows={2}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t("consultation.followUpPlaceholder")}
        />
        <Button
          type="primary"
          loading={mutation.isPending}
          disabled={disabled || !prompt.trim()}
          onClick={submit}
        >
          {t("consultation.followUpSubmit")}
        </Button>
        {disabled ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("consultation.followUpBlocked")}
          </Typography.Text>
        ) : (
          // 预告按需票拟的存在：否则用户在追问前无从知道还能请首辅汇总
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("consultation.followUpSynthesisHint")}
          </Typography.Text>
        )}
        {mutation.error && <PageQueryError error={mutation.error} />}
      </Space>
    </GlowCard>
  );
}

/** 综合/票拟的署名：指定了首席顾问就报其名，否则是通用「首席顾问」。 */
function synthesizerLabel(round: ConsultationRound, t: TFunc): string {
  if (!round.synthesizer_name) return t("consultation.synthesizerDefault");
  return round.synthesizer_department
    ? `${round.synthesizer_name} (${round.synthesizer_department})`
    : round.synthesizer_name;
}
