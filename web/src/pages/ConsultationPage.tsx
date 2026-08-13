import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Input, Button, Tag, Select, Space, Typography, Empty, List, theme } from "antd";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import {
  useConsultationLiveUpdates,
  useConsultations,
  useCreateConsultation,
} from "../hooks/useConsultation";
import { usePersonas } from "../hooks/usePersonas";
import type { ConsultationRequest, ConsultationResponse } from "../api/types";
import { useT } from "../i18n";
import PageQueryError from "../components/states/PageQueryError";

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
  const [topic, setTopic] = useState("");
  const [context, setContext] = useState("");
  const [selectedPersonas, setSelectedPersonas] = useState<string[]>([]);
  const [synthesizer, setSynthesizer] = useState<string | undefined>(undefined);

  const personasQuery = usePersonas();
  const { data: personas } = personasQuery;
  const createMutation = useCreateConsultation();
  const historyQuery = useConsultations();
  // 列表页也订阅：进行中的廷议状态变化即时反映在历史列表上
  useConsultationLiveUpdates(null);

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
    if (synthesizer) body.synthesizer_persona_id = synthesizer;
    createMutation.mutate(body, {
      onSuccess: (resp) => {
        const id = resp.data?.id;
        // 跳独立详情页：长内容不再和表单、历史列表挤在一页（issue #54）
        if (id) navigate(`/consultation/${id}`);
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
            <div>
              <Typography.Text style={{ fontSize: 13, color: token.colorTextSecondary }}>
                {t("consultation.synthesizer")}
              </Typography.Text>
              <Select
                allowClear
                style={{ width: "100%", marginTop: 4 }}
                placeholder={t("consultation.synthesizerPlaceholder")}
                value={synthesizer}
                onChange={setSynthesizer}
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
                  style={{ cursor: "pointer" }}
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
