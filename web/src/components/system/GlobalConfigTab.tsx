import { useState, useEffect, useCallback } from "react";
import { Alert, Row, Col, Card, Input, InputNumber, Button, Spin, notification, theme } from "antd";
import {
  useAgentConfig,
  useUpdateAgentConfig,
  useUpdateWorkspaceDir,
  useWorkspaceDir,
} from "../../hooks/useConfig";
import type { AgentConfigUpdateRequest } from "../../api/types";
import { useT } from "../../i18n";
import PageQueryError from "../states/PageQueryError";

export default function GlobalConfigTab() {
  const t = useT();
  const { token } = theme.useToken();
  const agentConfigQuery = useAgentConfig();
  const { data: agentConfigData } = agentConfigQuery;
  const updateAgentMutation = useUpdateAgentConfig();
  const [agentForm, setAgentForm] = useState<AgentConfigUpdateRequest>({});
  const workspaceQuery = useWorkspaceDir();
  const updateWorkspaceMutation = useUpdateWorkspaceDir();
  const [workspaceInput, setWorkspaceInput] = useState("");

  useEffect(() => {
    const saved = workspaceQuery.data?.workspace_dir;
    if (saved) setWorkspaceInput((prev) => prev || saved);
  }, [workspaceQuery.data?.workspace_dir]);

  const handleWorkspaceApply = useCallback(() => {
    updateWorkspaceMutation.mutate(workspaceInput.trim(), {
      onSuccess: (info) => {
        notification.success({
          message: t("system.globalConfig.workspaceSaved"),
          description: info.pending_restart
            ? t("system.globalConfig.workspacePendingRestart")
            : undefined,
        });
      },
      onError: (err: unknown) => {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          String(err);
        notification.error({
          message: t("system.globalConfig.workspaceFailed"),
          description: detail,
        });
      },
    });
  }, [updateWorkspaceMutation, workspaceInput, t]);

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
      agentForm.agent_max_concurrency !== undefined &&
      agentForm.agent_max_concurrency !== agentConfigData.agent_max_concurrency
    )
      payload.agent_max_concurrency = agentForm.agent_max_concurrency;
    if (
      agentForm.agent_retry_limit !== undefined &&
      agentForm.agent_retry_limit !== agentConfigData.agent_retry_limit
    )
      payload.agent_retry_limit = agentForm.agent_retry_limit;
    if (agentForm.agent_token_budget !== agentConfigData.agent_token_budget)
      payload.agent_token_budget = agentForm.agent_token_budget;
    if (agentForm.agent_cost_budget_cny !== agentConfigData.agent_cost_budget_cny)
      payload.agent_cost_budget_cny = agentForm.agent_cost_budget_cny;
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

  if (agentConfigQuery.error) {
    return (
      <PageQueryError
        error={agentConfigQuery.error}
        onRetry={() => void agentConfigQuery.refetch()}
      />
    );
  }

  if (agentConfigQuery.isLoading) {
    return <Spin />;
  }

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
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>{t("system.globalConfig.agentMaxConcurrency")}</div>
            <InputNumber
              min={1}
              max={8}
              value={agentForm.agent_max_concurrency}
              onChange={(v) => setAgentForm((prev) => ({ ...prev, agent_max_concurrency: v ?? 1 }))}
              style={{ width: "100%" }}
            />
          </div>
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>{t("system.globalConfig.agentRetryLimit")}</div>
            <InputNumber
              min={0}
              max={10}
              value={agentForm.agent_retry_limit}
              onChange={(v) => setAgentForm((prev) => ({ ...prev, agent_retry_limit: v ?? 0 }))}
              style={{ width: "100%" }}
            />
          </div>
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>{t("system.globalConfig.agentCostBudget")}</div>
            <InputNumber
              min={0}
              step={0.01}
              value={agentForm.agent_cost_budget_cny}
              placeholder={t("system.globalConfig.unlimited")}
              onChange={(v) =>
                setAgentForm((prev) => ({ ...prev, agent_cost_budget_cny: v ?? null }))
              }
              style={{ width: "100%" }}
            />
          </div>
          <div style={{ marginBottom: 16 }}>
            <div style={labelStyle}>{t("system.globalConfig.agentTokenBudget")}</div>
            <InputNumber
              min={1}
              value={agentForm.agent_token_budget}
              placeholder={t("system.globalConfig.unlimited")}
              onChange={(v) =>
                setAgentForm((prev) => ({ ...prev, agent_token_budget: v ?? null }))
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
      <Col xs={24} md={12} lg={8} style={{ marginTop: 16 }}>
        <Card title={t("system.globalConfig.workspaceSection")} size="small">
          <div style={labelStyle}>{t("system.globalConfig.workspaceDir")}</div>
          <Input
            value={workspaceInput}
            placeholder={workspaceQuery.data?.effective}
            onChange={(e) => setWorkspaceInput(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <div style={{ ...labelStyle, marginBottom: 12 }}>
            {t("system.globalConfig.workspaceHelp")}
          </div>
          {workspaceQuery.data?.pending_restart && (
            <Alert
              type="warning"
              showIcon
              message={t("system.globalConfig.workspacePendingRestart")}
              style={{ marginBottom: 12 }}
            />
          )}
          <Button
            loading={updateWorkspaceMutation.isPending}
            disabled={!workspaceInput.trim()}
            onClick={handleWorkspaceApply}
          >
            {t("system.globalConfig.apply")}
          </Button>
        </Card>
      </Col>
    </Row>
  );
}
