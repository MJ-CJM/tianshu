import { useState, useEffect, useCallback } from "react";
import { Row, Col, Card, InputNumber, Button, notification, theme } from "antd";
import { useAgentConfig, useUpdateAgentConfig } from "../../hooks/useConfig";
import type { AgentConfigUpdateRequest } from "../../api/types";
import { useT } from "../../i18n";

export default function GlobalConfigTab() {
  const t = useT();
  const { token } = theme.useToken();
  const { data: agentConfigData } = useAgentConfig();
  const updateAgentMutation = useUpdateAgentConfig();
  const [agentForm, setAgentForm] = useState<AgentConfigUpdateRequest>({});

  useEffect(() => {
    if (agentConfigData) {
      setAgentForm((prev) => {
        if (Object.keys(prev).length === 0) {
          return { ...agentConfigData };
        }
        return prev;
      });
    }
  }, [agentConfigData]);

  const handleApply = useCallback(() => {
    if (!agentConfigData) return;
    const payload: AgentConfigUpdateRequest = {};
    if (
      agentForm.agent_max_iterations !== undefined &&
      agentForm.agent_max_iterations !== agentConfigData.agent_max_iterations
    )
      payload.agent_max_iterations = agentForm.agent_max_iterations;
    if (
      agentForm.agent_timeout_seconds !== undefined &&
      agentForm.agent_timeout_seconds !== agentConfigData.agent_timeout_seconds
    )
      payload.agent_timeout_seconds = agentForm.agent_timeout_seconds;
    if (
      agentForm.skills_char_budget !== undefined &&
      agentForm.skills_char_budget !== agentConfigData.skills_char_budget
    )
      payload.skills_char_budget = agentForm.skills_char_budget;

    if (Object.keys(payload).length === 0) {
      notification.info({ message: t("system.toast.noChanges") });
      return;
    }
    updateAgentMutation.mutate(payload, {
      onSuccess: (data) => {
        notification.success({ message: t("system.toast.agentParamsUpdated") });
        setAgentForm({ ...data });
      },
    });
  }, [agentForm, agentConfigData, updateAgentMutation, t]);

  const labelStyle: React.CSSProperties = {
    marginBottom: 4,
    fontSize: 13,
    color: token.colorTextTertiary,
  };

  return (
    <Row gutter={16}>
      <Col xs={24} md={12} lg={8}>
        <Card title={t("system.globalConfig.agentSection")} size="small">
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>{t("system.globalConfig.agentMaxIter")}</div>
            <InputNumber
              min={1}
              max={200}
              step={1}
              value={agentForm.agent_max_iterations}
              onChange={(v) =>
                setAgentForm((prev) => ({
                  ...prev,
                  agent_max_iterations: v ?? 20,
                }))
              }
              style={{ width: "100%" }}
            />
          </div>
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>{t("system.globalConfig.agentTimeout")}</div>
            <InputNumber
              min={10}
              max={3600}
              step={10}
              value={agentForm.agent_timeout_seconds}
              onChange={(v) =>
                setAgentForm((prev) => ({
                  ...prev,
                  agent_timeout_seconds: v ?? 300,
                }))
              }
              style={{ width: "100%" }}
            />
          </div>
        </Card>
      </Col>
      <Col xs={24} md={12} lg={8}>
        <Card title={t("system.globalConfig.skillSection")} size="small">
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>{t("system.globalConfig.skillCharBudget")}</div>
            <InputNumber
              min={1000}
              max={500000}
              step={1000}
              value={agentForm.skills_char_budget}
              onChange={(v) =>
                setAgentForm((prev) => ({
                  ...prev,
                  skills_char_budget: v ?? 30000,
                }))
              }
              style={{ width: "100%" }}
            />
          </div>
        </Card>
      </Col>
      <Col xs={24} lg={8} style={{ display: "flex", alignItems: "flex-start", paddingTop: 38 }}>
        <Button
          type="primary"
          loading={updateAgentMutation.isPending}
          onClick={handleApply}
        >
          {t("system.globalConfig.apply")}
        </Button>
      </Col>
    </Row>
  );
}
