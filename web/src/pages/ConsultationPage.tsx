import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Input,
  Button,
  Tag,
  Spin,
  Result,
  Select,
  Space,
  Typography,
  Alert,
  Empty,
  List,
  Progress,
  theme,
} from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import {
  useConsultation,
  useConsultationLiveUpdates,
  useConsultations,
  useCreateConsultation,
} from "../hooks/useConsultation";
import { usePersonas } from "../hooks/usePersonas";
import type {
  ConsultationRequest,
  ConsultationResponse,
  PersonaOpinion,
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

export default function ConsultationPage() {
  const t = useT();
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const { consultationId } = useParams<{ consultationId: string }>();
  const [topic, setTopic] = useState("");
  const [context, setContext] = useState("");
  const [selectedPersonas, setSelectedPersonas] = useState<string[]>([]);

  const personasQuery = usePersonas();
  const { data: personas } = personasQuery;
  const createMutation = useCreateConsultation();
  const activeId = consultationId ?? null;
  const consultationQuery = useConsultation(activeId);
  const { data: consultation, isLoading: consultationLoading } = consultationQuery;
  const historyQuery = useConsultations();
  useConsultationLiveUpdates(activeId);

  if (personasQuery.error) {
    return (
      <PageContainer title={t("consultation.title")}>
        <PageQueryError
          error={personasQuery.error}
          onRetry={() => void personasQuery.refetch()}
        />
      </PageContainer>
    );
  }

  const handleSubmit = () => {
    if (!topic.trim() || selectedPersonas.length === 0) return;
    const body: ConsultationRequest = {
      topic: topic.trim(),
      persona_ids: selectedPersonas,
    };
    if (context.trim()) body.context = context.trim();
    createMutation.mutate(body, {
      onSuccess: (resp) => {
        const id = resp.data?.id;
        // id 进 URL：刷新、分享、回退都能找回这场廷议（issue #52）
        if (id) navigate(`/consultation/${id}`);
      },
    });
  };

  const personaOptions = (personas ?? []).map((p) => ({
    value: p.id,
    label: `${p.name} (${p.department})`,
  }));

  const expectedCount = consultation?.request?.persona_ids?.length ?? 0;
  const doneCount = consultation?.opinions.length ?? 0;
  const isActive = consultation?.status === "pending" || consultation?.status === "running";

  return (
    <PageContainer title={t("consultation.title")}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        {/* Submit form */}
        <GlowCard title={t("consultation.submit")}>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <div>
              <Typography.Text style={{ fontSize: 13, color: token.colorTextSecondary }}>
                {t("consultation.topic")} *
              </Typography.Text>
              <Input.TextArea
                rows={2}
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder={t("consultation.topicPlaceholder")}
                style={{ marginTop: 4 }}
              />
            </div>
            <div>
              <Typography.Text style={{ fontSize: 13, color: token.colorTextSecondary }}>
                {t("consultation.context")}
              </Typography.Text>
              <Input.TextArea
                rows={2}
                value={context}
                onChange={(e) => setContext(e.target.value)}
                placeholder={t("consultation.contextPlaceholder")}
                style={{ marginTop: 4 }}
              />
            </div>
            <div>
              <Typography.Text style={{ fontSize: 13, color: token.colorTextSecondary }}>
                {t("consultation.participants")} *
              </Typography.Text>
              <Select
                mode="multiple"
                style={{ width: "100%", marginTop: 4 }}
                placeholder={t("consultation.participantsPlaceholder")}
                value={selectedPersonas}
                onChange={setSelectedPersonas}
                options={personaOptions}
              />
            </div>
            <Button
              type="primary"
              loading={createMutation.isPending}
              onClick={handleSubmit}
              disabled={!topic.trim() || selectedPersonas.length === 0}
            >
              {t("consultation.submit")}
            </Button>
            {createMutation.error && <PageQueryError error={createMutation.error} />}
          </Space>
        </GlowCard>

        {/* Consultation detail */}
        {activeId && (
          <>
            {consultationQuery.error && (
              <PageQueryError
                error={consultationQuery.error}
                onRetry={() => void consultationQuery.refetch()}
              />
            )}

            {consultationLoading && !consultation && (
              <div style={{ textAlign: "center", padding: 48 }}>
                <Spin size="large" />
              </div>
            )}

            {isActive && (
              <GlowCard>
                <div style={{ textAlign: "center", padding: 24 }}>
                  <Spin />
                  <Typography.Text style={{ display: "block", marginTop: 12 }}>
                    {consultation?.status === "pending"
                      ? t("consultation.preparing")
                      : t("consultation.running")}
                  </Typography.Text>
                  {expectedCount > 0 && (
                    <div style={{ maxWidth: 320, margin: "16px auto 0" }}>
                      <Progress
                        percent={Math.round((doneCount / expectedCount) * 100)}
                        size="small"
                        status="active"
                      />
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {t("consultation.progress", {
                          done: doneCount,
                          total: expectedCount,
                        })}
                      </Typography.Text>
                    </div>
                  )}
                </div>
              </GlowCard>
            )}

            {consultation?.status === "failed" && (
              <Result
                status="error"
                title={t("consultation.failedTitle")}
                subTitle={consultation.error || t("consultation.failedSubtitle")}
              />
            )}

            {/* 已到达的意见即时展示——不必等整场廷议结束 */}
            {consultation && consultation.status !== "failed" && (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                {consultation.error && consultation.status === "completed" && (
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
                    <div
                      className="memorial-markdown"
                      style={{
                        color: token.colorText,
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: 13,
                        lineHeight: 1.7,
                      }}
                    >
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {opinion.opinion}
                      </ReactMarkdown>
                    </div>
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

                {/* Synthesis */}
                {consultation.synthesis && (
                  <GlowCard title={t("consultation.synthesisTitle")}>
                    <div
                      className="memorial-markdown"
                      style={{
                        color: token.colorText,
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize: 13,
                        lineHeight: 1.7,
                      }}
                    >
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {consultation.synthesis}
                      </ReactMarkdown>
                    </div>
                  </GlowCard>
                )}

                {/* Decision */}
                {consultation.decision && (
                  <GlowCard
                    title={t("consultation.decisionTitle")}
                    style={{
                      borderLeft: `3px solid ${token.colorSuccess}`,
                    }}
                  >
                    <Typography.Text
                      strong
                      style={{ fontSize: 15, color: token.colorText }}
                    >
                      {consultation.decision}
                    </Typography.Text>
                  </GlowCard>
                )}
              </Space>
            )}
          </>
        )}

        {/* 历史廷议——从后端拉取，刷新/重启/换设备都能找回 */}
        <GlowCard title={t("consultation.history")}>
          {historyQuery.error ? (
            <PageQueryError
              error={historyQuery.error}
              onRetry={() => void historyQuery.refetch()}
            />
          ) : (
            <List
              size="small"
              loading={historyQuery.isLoading}
              dataSource={historyQuery.data ?? []}
              locale={{ emptyText: <Empty description={t("consultation.historyEmpty")} /> }}
              renderItem={(item: ConsultationResponse) => (
                <List.Item
                  onClick={() => navigate(`/consultation/${item.id}`)}
                  style={{
                    cursor: "pointer",
                    background: item.id === activeId ? token.colorFillTertiary : undefined,
                  }}
                >
                  <List.Item.Meta
                    title={
                      <Space>
                        <Tag color={STATUS_COLORS[item.status] ?? "default"}>
                          {t(`consultation.status.${item.status}`)}
                        </Tag>
                        <span>{item.request?.topic ?? item.id}</span>
                      </Space>
                    }
                    description={
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {new Date(item.created_at).toLocaleString()}
                      </Typography.Text>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </GlowCard>
      </Space>
    </PageContainer>
  );
}
