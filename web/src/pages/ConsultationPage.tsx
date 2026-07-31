import { useState } from "react";
import { Input, Button, Tag, Spin, Result, Select, Space, Typography, theme } from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import { useConsultation, useCreateConsultation } from "../hooks/useConsultation";
import { usePersonas } from "../hooks/usePersonas";
import type { ConsultationRequest, PersonaOpinion } from "../api/types";
import { useT } from "../i18n";
import PageQueryError from "../components/states/PageQueryError";

const STANCE_COLORS: Record<string, string> = {
  support: "green",
  oppose: "red",
  conditional: "orange",
};

export default function ConsultationPage() {
  const t = useT();
  const { token } = theme.useToken();
  const [topic, setTopic] = useState("");
  const [context, setContext] = useState("");
  const [selectedPersonas, setSelectedPersonas] = useState<string[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);

  const personasQuery = usePersonas();
  const { data: personas } = personasQuery;
  const createMutation = useCreateConsultation();
  const consultationQuery = useConsultation(activeId);
  const { data: consultation, isLoading: consultationLoading } = consultationQuery;

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
        if (id) {
          setActiveId(id);
          setHistory((prev) => [...prev, id]);
        }
      },
    });
  };

  const personaOptions = (personas ?? []).map((p) => ({
    value: p.id,
    label: `${p.name} (${p.department})`,
  }));

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
          </Space>
        </GlowCard>

        {/* History navigation */}
        {history.length > 1 && (
          <Space wrap>
            <Typography.Text type="secondary">{t("consultation.history")}</Typography.Text>
            {history.map((id, i) => (
              <Tag
                key={id}
                color={id === activeId ? "blue" : "default"}
                style={{ cursor: "pointer" }}
                onClick={() => setActiveId(id)}
              >
                {t("consultation.round", { n: i + 1 })}
              </Tag>
            ))}
          </Space>
        )}

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

            {consultation?.status === "pending" && (
              <GlowCard>
                <div style={{ textAlign: "center", padding: 24 }}>
                  <Spin />
                  <Typography.Text style={{ display: "block", marginTop: 12 }}>
                    {t("consultation.preparing")}
                  </Typography.Text>
                </div>
              </GlowCard>
            )}

            {consultation?.status === "running" && (
              <GlowCard>
                <div style={{ textAlign: "center", padding: 24 }}>
                  <Spin />
                  <Typography.Text style={{ display: "block", marginTop: 12 }}>
                    {t("consultation.running")}
                  </Typography.Text>
                </div>
              </GlowCard>
            )}

            {consultation?.status === "failed" && (
              <Result status="error" title={t("consultation.failedTitle")} subTitle={t("consultation.failedSubtitle")} />
            )}

            {consultation?.status === "completed" && (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                {/* Opinions */}
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
      </Space>
    </PageContainer>
  );
}
