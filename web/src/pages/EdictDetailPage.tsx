import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Input,
  Modal,
  Typography,
  Tag,
  Space,
  Popconfirm,
  Collapse,
  Descriptions,
  Table,
  message,
  theme,
  Tooltip,
} from "antd";
import {
  ArrowLeftOutlined,
  SendOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  EditOutlined,
  StopOutlined,
  DeploymentUnitOutlined,
  BulbOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  LikeOutlined,
  DislikeOutlined,
} from "@ant-design/icons";
import { useEdictDetail } from "../hooks/useEdictDetail";
import {
  followUpEdict,
  updateEdictStatus,
  updateEdict,
  pauseEdict,
  resumeEdict,
} from "../api/edicts";
import { submitUniverseFeedback } from "../api/universe";
import { isApiProblem } from "../api/client";
import PageContainer from "../components/common/PageContainer";
import PageDataState from "../components/states/PageDataState";
import GlowCard from "../components/common/GlowCard";
import MonoText from "../components/common/MonoText";
import SemanticTag from "../components/common/SemanticTag";
import MemorialCard from "../components/memorial/MemorialCard";
import UsageDisplay from "../components/memorial/UsageDisplay";
import EventTimeline from "../components/memorial/EventTimeline";
import OuterLoopTimeline from "../components/edict/OuterLoopTimeline";
import SupervisionReportCard from "../components/edict/SupervisionReportCard";
import FollowUpOverridePanel from "../components/edict/FollowUpOverridePanel";
import SteerPanel from "../components/edict/SteerPanel";
import type { FollowUpOverrideValue } from "../components/edict/FollowUpOverridePanel";
import { PolicyTimeline } from "../components/policy/PolicyTimeline";
import EdictDurableGovernance from "../components/governance/EdictDurableGovernance";
import { useDagByEdict } from "../hooks/useDag";
import DagBattleMap from "../components/dag/DagBattleMap";
import { formatTime, truncateId } from "../utils/format";
import {
  EDICT_STATUS_COLORS,
  PRIORITY_COLORS,
  useEdictStatusLabels,
} from "../utils/constants";
import { useT } from "../i18n";
import type { DAGExecution, UsageSummary } from "../api/types";

export default function EdictDetailPage() {
  const { edictId } = useParams<{ edictId: string }>();
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const {
    detail,
    edict,
    memorials,
    events,
    status,
    problem,
    refetch,
    resolveDecision,
    replay,
  } = useEdictDetail(edictId ?? "");
  const t = useT();
  const edictStatusLabels = useEdictStatusLabels();
  const [instruction, setInstruction] = useState("");
  const [followUpOverride, setFollowUpOverride] =
    useState<FollowUpOverrideValue>({});
  const [submitting, setSubmitting] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editGoal, setEditGoal] = useState("");
  const [editContext, setEditContext] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const { data: dagExecution } = useDagByEdict(edictId);
  const hasDag =
    dagExecution && dagExecution.nodes && dagExecution.nodes.length > 1;

  // Extract plan event for display
  const planEvent = useMemo(() => {
    // Find the most recent plan event (pending_review or completed)
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i]!;
      if (
        e.event_type === "plan.pending_review" ||
        e.event_type === "plan.completed"
      ) {
        return e;
      }
    }
    return null;
  }, [events]);

  const planPayload = useMemo(
    () => planEvent?.payload?.plan as Record<string, unknown> | undefined,
    [planEvent],
  );
  const planTasks = useMemo(() => {
    return (planPayload?.tasks as Array<Record<string, unknown>>) ?? [];
  }, [planPayload]);
  const planFallbackReason = useMemo(() => {
    if (planPayload?.planning_mode !== "fallback") return null;
    const reason = String(planPayload.fallback_reason ?? "unknown");
    return [
      "llm_disabled",
      "empty_response",
      "invalid_response",
      "empty_plan",
      "llm_error",
    ].includes(reason)
      ? reason
      : "unknown";
  }, [planPayload]);

  const isPendingPlanReview = useMemo(() => {
    const hasPending = events.some(
      (e) => e.event_type === "plan.pending_review",
    );
    const hasResolution = events.some(
      (e) =>
        e.event_type === "plan.approved" ||
        e.event_type === "plan.rejected" ||
        (e.event_type === "plan.completed" &&
          planEvent?.event_type !== "plan.pending_review"),
    );
    return hasPending && !hasResolution;
  }, [events, planEvent]);

  // DAG 规划预演:多任务计划在执行开始前(尚无真实 DAGExecution)由 plan 合成沙盘,
  // 执行开始后 by-edict 轮询拿到真实记录,无缝切换为实时战况。
  const previewExecution = useMemo<DAGExecution | null>(() => {
    if (!edictId || planTasks.length <= 1) return null;
    const previewId = `preview-${edictId}`;
    return {
      id: previewId,
      edict_id: edictId,
      plan_json: "",
      status: "preview",
      root_memorial_id: null,
      max_concurrency: 1,
      created_at: "",
      completed_at: null,
      nodes: planTasks.map((task) => ({
        node_id: String(task.task_id ?? ""),
        dag_execution_id: previewId,
        description: String(task.description ?? ""),
        depends_on: (task.depends_on as string[]) ?? [],
        status: "pending" as const,
        assigned_official: (task.assigned_official as string | null) ?? null,
        assigned_worker: null,
        tools_required: (task.tools_required as string[]) ?? [],
        memorial_id: null,
        started_at: null,
        completed_at: null,
        error: null,
      })),
    };
  }, [edictId, planTasks]);

  const inlineDag = hasDag ? dagExecution! : previewExecution;
  const inlineDagIsPreview = !hasDag && !!previewExecution;

  // 预演 → 实时的切换保障:by-edict 404 后轮询即停,而 WS 断线期间的 dag.*
  // 事件会丢——多任务计划执行活跃时主动重询,拿到真实 DAGExecution 即停。
  const queryClient = useQueryClient();
  const awaitingRealDag =
    inlineDagIsPreview &&
    memorials.some((m) =>
      ["running", "submitted", "scheduled", "planning", "auditing"].includes(
        m.status,
      ),
    );
  useEffect(() => {
    if (!awaitingRealDag || !edictId) return;
    const timer = setInterval(() => {
      queryClient.invalidateQueries({ queryKey: ["dag", "by-edict", edictId] });
    }, 4000);
    return () => clearInterval(timer);
  }, [awaitingRealDag, edictId, queryClient]);

  const [feedbackSent, setFeedbackSent] = useState<Record<string, number>>({});

  const hasActiveMemorial = memorials.some((m) =>
    ["running", "submitted", "scheduled", "planning", "auditing"].includes(
      m.status,
    ),
  );
  const hasPendingReview = memorials.some((m) => m.review_status === "pending");
  const canFollowUp =
    edict?.status === "open" && !hasActiveMemorial && !hasPendingReview;

  const aggregatedUsage = useMemo<UsageSummary>(() => {
    return memorials.reduce<UsageSummary>(
      (acc, m) => ({
        prompt_tokens: acc.prompt_tokens + (m.usage?.prompt_tokens ?? 0),
        completion_tokens:
          acc.completion_tokens + (m.usage?.completion_tokens ?? 0),
        total_tokens: acc.total_tokens + (m.usage?.total_tokens ?? 0),
        cache_read_tokens:
          (acc.cache_read_tokens ?? 0) + (m.usage?.cache_read_tokens ?? 0),
        cost_cny: (acc.cost_cny ?? 0) + (m.usage?.cost_cny ?? 0),
        // 取最近一条 memorial 的非空回显，跟后端 _accumulate_usage 语义一致
        // （多轮一致；fallback 切了模型时保留切后值）
        actual_model: m.usage?.actual_model ?? acc.actual_model ?? null,
        upstream_provider:
          m.usage?.upstream_provider ?? acc.upstream_provider ?? null,
      }),
      {
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
        cache_read_tokens: 0,
        cost_cny: 0,
        actual_model: null,
        upstream_provider: null,
      },
    );
  }, [memorials]);

  const hasUsage = aggregatedUsage.total_tokens > 0;

  const handleFollowUp = async () => {
    if (!edictId || !instruction.trim()) return;
    setSubmitting(true);
    try {
      await followUpEdict(edictId, {
        instruction: instruction.trim(),
        ...(followUpOverride.runtime_override
          ? { runtime_override: followUpOverride.runtime_override }
          : {}),
        ...(followUpOverride.acceptance_override
          ? { acceptance_override: followUpOverride.acceptance_override }
          : {}),
      });
      setInstruction("");
      setFollowUpOverride({});
      refetch();
    } catch {
      message.error(t("toast.followupFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleClose = async () => {
    if (!edictId) return;
    try {
      await updateEdictStatus(edictId, "completed");
      refetch();
      message.success(t("toast.edictClosed"));
    } catch {
      message.error(t("toast.closeFailed"));
    }
  };

  const handleCancel = async () => {
    if (!edictId) return;
    try {
      await updateEdictStatus(edictId, "cancelled");
      refetch();
      message.success(t("toast.edictCancelled"));
    } catch {
      message.error(t("toast.cancelFailed"));
    }
  };

  const handlePause = async () => {
    if (!edictId) return;
    try {
      await pauseEdict(edictId);
      refetch();
      message.success(t("toast.edictPauseRequested"));
    } catch (err: unknown) {
      const detail = isApiProblem(err) ? err.message : null;
      message.error(
        detail
          ? `${t("toast.pauseFailed")}：${detail}`
          : t("toast.pauseFailed"),
      );
    }
  };

  const handleResume = async () => {
    if (!edictId) return;
    try {
      await resumeEdict(edictId);
      refetch();
      message.success(t("toast.edictResumed"));
    } catch (err: unknown) {
      const detail = isApiProblem(err) ? err.message : null;
      message.error(
        detail
          ? `${t("toast.resumeFailed")}：${detail}`
          : t("toast.resumeFailed"),
      );
    }
  };

  const openEditModal = () => {
    if (!edict) return;
    setEditTitle(edict.title);
    setEditGoal(edict.goal);
    setEditContext(edict.context ?? "");
    setEditOpen(true);
  };

  const handleEditSave = async () => {
    if (!edictId) return;
    setEditSaving(true);
    try {
      await updateEdict(edictId, {
        title: editTitle.trim() || undefined,
        goal: editGoal.trim() || undefined,
        context: editContext.trim() || undefined,
      });
      setEditOpen(false);
      refetch();
      message.success(t("toast.edictUpdated"));
    } catch {
      message.error(t("toast.updateFailed"));
    } finally {
      setEditSaving(false);
    }
  };

  if (
    status === "loading" ||
    status === "error" ||
    status === "permission-denied" ||
    status === "service-unavailable"
  ) {
    return (
      <PageContainer title={t("page.edictDetail.title")}>
        <PageDataState
          status={status}
          data={null}
          problem={problem}
          isEmpty={() => true}
          onRetry={refetch}
        >
          {() => null}
        </PageDataState>
      </PageContainer>
    );
  }

  if (!edict) {
    return (
      <PageContainer title={t("page.edictDetail.title")}>
        <Typography.Text type="secondary">
          {t("page.edictDetail.notFound")}
        </Typography.Text>
      </PageContainer>
    );
  }

  return (
    <PageContainer
      title={t("page.edictDetail.title")}
      extra={
        <Space>
          {hasDag && (
            <Button
              icon={<DeploymentUnitOutlined />}
              onClick={() => navigate(`/dag/${dagExecution!.id}`)}
            >
              {t("page.edictDetail.viewBattleMap")}
            </Button>
          )}
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate("/approvals")}
          >
            {t("page.edictDetail.backToList")}
          </Button>
        </Space>
      }
    >
      {status === "stale" ? (
        <div style={{ marginBottom: 24 }}>
          <PageDataState
            status="stale"
            data={detail}
            problem={problem}
            isEmpty={() => false}
            onRetry={refetch}
          >
            {() => null}
          </PageDataState>
        </div>
      ) : null}

      <GlowCard
        title={
          <Space size="middle">
            <span>{edict.title || t("entity.edict")}</span>
            <SemanticTag colorVar={EDICT_STATUS_COLORS[edict.status]}>
              {edictStatusLabels[edict.status]}
            </SemanticTag>
            {edict.runtime.lifecycle_phase !== "active" && (
              <Tag
                color={
                  edict.runtime.lifecycle_phase === "paused"
                    ? "warning"
                    : edict.runtime.lifecycle_phase === "winding_down"
                      ? "orange"
                      : "default"
                }
              >
                {t(`lifecycle.${edict.runtime.lifecycle_phase}`)}
              </Tag>
            )}
            {edict.status === "open" && (
              <Button
                type="text"
                size="small"
                icon={<EditOutlined />}
                onClick={openEditModal}
              />
            )}
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <div style={{ marginBottom: 12 }}>
          <Typography.Text style={{ color: token.colorText, fontSize: 15 }}>
            {edict.goal}
          </Typography.Text>
        </div>
        {edict.context && (
          <div style={{ marginBottom: 12 }}>
            <Typography.Text
              style={{ color: token.colorTextSecondary, fontSize: 13 }}
            >
              {edict.context}
            </Typography.Text>
          </div>
        )}
        <Descriptions
          size="small"
          column={{ xs: 1, sm: 2, md: 3, lg: 4 }}
          style={{ marginBottom: 12 }}
          items={[
            {
              key: "priority",
              label: t("page.edictDetail.details.priority"),
              children: (
                <SemanticTag
                  colorVar={
                    PRIORITY_COLORS[edict.priority] ?? "var(--ts-color-info)"
                  }
                >
                  {t(`priority.${edict.priority}`)}
                </SemanticTag>
              ),
            },
            {
              key: "review_policy",
              label: t("page.edictDetail.details.reviewPolicy"),
              children: t(`reviewPolicy.${edict.review_policy}`),
            },
            {
              key: "schedule",
              label: t("page.edictDetail.details.schedule"),
              children: (
                <span>
                  {edict.schedule?.type
                    ? t(`scheduleType.${edict.schedule.type}`)
                    : t("scheduleType.immediate")}
                  {edict.schedule?.cron && (
                    <MonoText
                      style={{
                        marginLeft: 6,
                        fontSize: 11,
                        color: token.colorTextSecondary,
                      }}
                    >
                      {edict.schedule.cron}
                    </MonoText>
                  )}
                  {edict.schedule?.at && (
                    <span
                      style={{
                        marginLeft: 6,
                        fontSize: 12,
                        color: token.colorTextSecondary,
                      }}
                    >
                      {formatTime(edict.schedule.at)}
                    </span>
                  )}
                </span>
              ),
            },
            {
              key: "source",
              label: t("page.edictDetail.details.source"),
              children: edict.source,
            },
            {
              key: "assigned_persona",
              label: t("page.edictDetail.details.executionMode"),
              children: edict.assigned_persona_id ? (
                <Tag color="orange">{edict.assigned_persona_id}</Tag>
              ) : (
                t("page.edictDetail.details.cabinetDecision")
              ),
            },
            ...(!edict.assigned_persona_id
              ? [
                  {
                    key: "planner_persona",
                    label: t("page.edictDetail.details.plannerPersona"),
                    children: edict.planner_persona_id ? (
                      <Tag color="purple">{edict.planner_persona_id}</Tag>
                    ) : planEvent?.payload?.planner_persona_id ? (
                      // 内阁决策默认规划官:实际主持规划的内阁官员(事件 payload 透出)
                      <Tag color="purple">
                        {String(planEvent.payload.planner_persona_id)}
                      </Tag>
                    ) : (
                      t("page.edictDetail.details.globalConfig")
                    ),
                  },
                ]
              : []),
          ]}
        />

        {edict.acceptance && (
          <div
            style={{
              marginBottom: 12,
              padding: "10px 12px",
              border: `1px solid ${token.colorPrimaryBorder}`,
              background: token.colorPrimaryBg,
              borderRadius: 6,
            }}
          >
            <Space size="small" wrap>
              <Tag color="purple" style={{ fontWeight: 600 }}>
                {t("page.edictDetail.details.longTaskTag")}
              </Tag>
              <Tag>
                {t("page.edictDetail.details.maxOuterRounds", {
                  n: edict.acceptance.max_outer_iterations ?? 5,
                })}
              </Tag>
              {edict.acceptance.deadline_seconds && (
                <Tag color="gold">
                  {t("page.edictDetail.details.timeBudgetMin", {
                    n: Math.round(edict.acceptance.deadline_seconds / 60),
                  })}
                </Tag>
              )}
              <Tag color="orange">
                {t("page.edictDetail.details.exhaustionPrefix")}
                {t(
                  `exhaustion.short${
                    {
                      escalate: "Escalate",
                      best_effort: "BestEffort",
                      fail: "Fail",
                    }[edict.acceptance.on_exhaustion ?? "escalate"]
                  }`,
                )}
              </Tag>
              {edict.acceptance.checks &&
                edict.acceptance.checks.length > 0 && (
                  <Tag color="cyan">
                    {t("page.edictDetail.details.checksCount", {
                      n: edict.acceptance.checks.length,
                    })}
                  </Tag>
                )}
              {(() => {
                const ids =
                  edict.acceptance.critic?.persona_ids ??
                  (edict.acceptance.critic?.persona_id
                    ? [edict.acceptance.critic.persona_id]
                    : []);
                if (ids.length === 0) return null;
                return (
                  <span>
                    <Typography.Text
                      type="secondary"
                      style={{ fontSize: 12, marginRight: 4 }}
                    >
                      {t("page.edictDetail.details.criticLabel")}
                    </Typography.Text>
                    {ids.map((pid) => (
                      <Tag key={pid} color="magenta" style={{ marginRight: 4 }}>
                        {pid}
                      </Tag>
                    ))}
                  </span>
                );
              })()}
            </Space>
          </div>
        )}

        {edict.constraints && edict.constraints.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Typography.Text
              style={{
                color: token.colorTextSecondary,
                fontSize: 12,
                marginRight: 8,
              }}
            >
              {t("page.edictDetail.details.constraints")}：
            </Typography.Text>
            {edict.constraints.map((c, i) => (
              <Tag key={i}>{c}</Tag>
            ))}
          </div>
        )}

        {edict.output_format && (
          <div style={{ marginBottom: 12 }}>
            <Typography.Text
              style={{ color: token.colorTextSecondary, fontSize: 12 }}
            >
              {t("page.edictDetail.details.outputFormat")}：
            </Typography.Text>
            <pre
              style={{
                marginTop: 4,
                padding: 8,
                background: "var(--ts-color-bg-subtle)",
                borderRadius: 4,
                fontSize: 12,
                border: `1px solid ${token.colorBorder}`,
                whiteSpace: "pre-wrap",
              }}
            >
              {edict.output_format}
            </pre>
          </div>
        )}

        <Collapse
          ghost
          size="small"
          style={{ marginBottom: 12 }}
          items={[
            {
              key: "runtime",
              label: (
                <Typography.Text
                  style={{ fontSize: 12, color: token.colorTextSecondary }}
                >
                  {t("page.edictDetail.details.runtimeSection")}
                </Typography.Text>
              ),
              children: (
                <Descriptions
                  size="small"
                  column={{ xs: 1, sm: 2, md: 3 }}
                  items={[
                    {
                      key: "timeout",
                      label: t("page.edictDetail.details.timeout"),
                      children: `${edict.runtime?.timeout_seconds ?? 300}s`,
                    },
                    {
                      key: "iterations",
                      label: t("page.edictDetail.details.maxIterations"),
                      children: edict.runtime?.max_iterations ?? 20,
                    },
                    {
                      key: "concurrency",
                      label: t("page.edictDetail.details.concurrency"),
                      children: edict.runtime?.max_concurrency ?? 1,
                    },
                    {
                      key: "retry",
                      label: t("page.edictDetail.details.retry"),
                      children: edict.runtime?.retry_limit ?? 0,
                    },
                    {
                      key: "token_budget",
                      label: t("page.edictDetail.details.tokenBudget"),
                      children:
                        edict.runtime?.token_budget ?? t("common2.unlimited"),
                    },
                    {
                      key: "cost_budget",
                      label: t("page.edictDetail.details.costBudget"),
                      children:
                        edict.runtime?.cost_budget_cny != null
                          ? `¥${edict.runtime.cost_budget_cny}`
                          : t("common2.unlimited"),
                    },
                    {
                      key: "time_budget",
                      label: t("page.edictDetail.details.timeBudget"),
                      children: edict.acceptance?.deadline_seconds
                        ? `${Math.round(edict.acceptance.deadline_seconds / 60)} ${t("unit.minutes")}`
                        : t("common2.unlimited"),
                    },
                  ]}
                />
              ),
            },
          ]}
        />

        <Space
          size={16}
          style={{ color: token.colorTextSecondary, fontSize: 12 }}
        >
          <span title={edict.id}>
            <MonoText style={{ color: token.colorTextSecondary, fontSize: 11 }}>
              {truncateId(edict.id, 12)}
            </MonoText>
          </span>
          <span>
            <ClockCircleOutlined style={{ marginRight: 4 }} />
            {formatTime(edict.created_at)}
          </span>
        </Space>
      </GlowCard>

      {detail ? (
        <Collapse
          ghost
          style={{ marginBottom: 24 }}
          items={[
            {
              key: "governance",
              label: t("page.edictDetail.auditSectionTitle"),
              forceRender: true,
              children: (
                <EdictDurableGovernance
                  detail={detail}
                  onResolve={resolveDecision}
                  onConflict={refetch}
                  onReplay={async (source) => {
                    const nextEdictId = await replay(source);
                    navigate(`/edicts/${encodeURIComponent(nextEdictId)}`);
                    return nextEdictId;
                  }}
                />
              ),
            },
          ]}
        />
      ) : null}

      {planEvent && planTasks.length > 0 && (
        <GlowCard
          title={
            <Space>
              <BulbOutlined />
              {t("page.edictDetail.planCardTitle")}
              {isPendingPlanReview && (
                <Tag color="warning">{t("status.needs_review")}</Tag>
              )}
              {!isPendingPlanReview &&
                planEvent.event_type === "plan.completed" && (
                  <Tag color="success">
                    {t("page.edictDetail.planExecuted")}
                  </Tag>
                )}
              {planFallbackReason && (
                <Tag color="warning">
                  {t("page.edictDetail.planFallbackTag")}
                </Tag>
              )}
            </Space>
          }
          style={{
            marginBottom: 24,
            borderLeft: "3px solid var(--ts-status-planning)",
          }}
        >
          {planFallbackReason && (
            <Alert
              type="warning"
              showIcon
              message={t("page.edictDetail.planFallbackMessage")}
              description={t(
                `page.edictDetail.planFallbackReason.${planFallbackReason}`,
              )}
              style={{ marginBottom: 12 }}
            />
          )}
          <Table
            size="small"
            pagination={false}
            dataSource={planTasks}
            rowKey="task_id"
            columns={[
              {
                title: t("page.edictDetail.planTaskColumn.task"),
                dataIndex: "description",
                ellipsis: true,
              },
              {
                title: t("page.edictDetail.planTaskColumn.executor"),
                dataIndex: "assigned_official",
                width: 120,
                render: (v: string) => <Tag color="blue">{v}</Tag>,
              },
              {
                title: t("page.edictDetail.planTaskColumn.depends"),
                dataIndex: "depends_on",
                width: 120,
                render: (v: string[]) =>
                  v && v.length > 0 ? v.join(", ") : "\u2014",
              },
              {
                title: t("page.edictDetail.planTaskColumn.tools"),
                dataIndex: "tools_required",
                width: 160,
                render: (v: string[]) =>
                  v && v.length > 0
                    ? v.map((t: string) => <Tag key={t}>{t}</Tag>)
                    : "\u2014",
              },
            ]}
          />

          {inlineDag && (
            <div
              style={{
                height: 360,
                marginTop: 16,
                border: "1px solid var(--ts-color-border)",
                borderRadius: 10,
                overflow: "hidden",
              }}
            >
              <DagBattleMap
                execution={inlineDag}
                preview={inlineDagIsPreview}
              />
            </div>
          )}
        </GlowCard>
      )}

      {memorials.map((memorial, index) => (
        <div key={memorial.id}>
          <MemorialCard memorial={memorial} index={index} readOnly />
          {edictId && edict?.acceptance && (
            <SupervisionReportCard edictId={edictId} memorialId={memorial.id} />
          )}
          {(memorial.status === "completed" ||
            memorial.status === "failed") && (
            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                marginBottom: 8,
                gap: 6,
              }}
            >
              <Tooltip title="好结果">
                <Button
                  size="small"
                  type={feedbackSent[memorial.id] === 1 ? "primary" : "text"}
                  icon={<LikeOutlined />}
                  onClick={async () => {
                    await submitUniverseFeedback(memorial.id, 1);
                    setFeedbackSent((prev) => ({ ...prev, [memorial.id]: 1 }));
                    void message.success("已反馈");
                  }}
                />
              </Tooltip>
              <Tooltip title="差结果">
                <Button
                  size="small"
                  type={feedbackSent[memorial.id] === -1 ? "primary" : "text"}
                  danger={feedbackSent[memorial.id] === -1}
                  icon={<DislikeOutlined />}
                  onClick={async () => {
                    await submitUniverseFeedback(memorial.id, -1);
                    setFeedbackSent((prev) => ({ ...prev, [memorial.id]: -1 }));
                    void message.success("已反馈");
                  }}
                />
              </Tooltip>
            </div>
          )}
        </div>
      ))}

      {hasUsage && <UsageDisplay usage={aggregatedUsage} />}

      {edictId &&
        edict.acceptance &&
        edict.runtime.lifecycle_phase === "active" &&
        hasActiveMemorial && <SteerPanel edictId={edictId} />}

      {canFollowUp && (
        <GlowCard
          title={t("page.edictDetail.followupTitle")}
          style={{ marginBottom: 24 }}
        >
          <div style={{ display: "flex", gap: 12, alignItems: "flex-end" }}>
            <Input.TextArea
              placeholder={t("page.edictDetail.followupPlaceholder")}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              onPressEnter={(e) => {
                if (e.ctrlKey || e.metaKey) handleFollowUp();
              }}
              disabled={submitting}
              rows={2}
              autoSize={{ minRows: 2, maxRows: 6 }}
              style={{ flex: 1 }}
            />
          </div>
          <FollowUpOverridePanel
            onChange={setFollowUpOverride}
            assignedPersonaId={edict?.assigned_persona_id ?? null}
          />
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 8,
              justifyContent: "space-between",
              alignItems: "center",
              marginTop: 12,
            }}
          >
            <div style={{ flex: 1, textAlign: "center" }}>
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={handleFollowUp}
                loading={submitting}
                disabled={!instruction.trim()}
              >
                {t("page.edictDetail.followupSubmit")}
              </Button>
            </div>
            {edict.status === "open" && (
              <Space size="small" wrap>
                {edict.acceptance &&
                  edict.runtime.lifecycle_phase === "active" &&
                  hasActiveMemorial && (
                    <Button
                      size="small"
                      icon={<PauseCircleOutlined />}
                      onClick={handlePause}
                    >
                      {t("button.pauseAfterRound")}
                    </Button>
                  )}
                {edict.acceptance &&
                  edict.runtime.lifecycle_phase === "paused" && (
                    <Button
                      size="small"
                      type="primary"
                      icon={<PlayCircleOutlined />}
                      onClick={handleResume}
                    >
                      {t("button.resume")}
                    </Button>
                  )}
                <Popconfirm
                  title={t("page.edictDetail.confirmClose.title")}
                  description={t("page.edictDetail.confirmClose.description")}
                  onConfirm={handleClose}
                  okText={t("common.confirm")}
                  cancelText={t("common.cancel")}
                >
                  <Button size="small" icon={<CheckOutlined />}>
                    {t("page.edictDetail.closeButton")}
                  </Button>
                </Popconfirm>
                <Popconfirm
                  title={t("page.edictDetail.confirmCancel.title")}
                  description={t("page.edictDetail.confirmCancel.description")}
                  onConfirm={handleCancel}
                  okText={t("common.confirm")}
                  cancelText={t("common.cancel")}
                >
                  <Button size="small" icon={<StopOutlined />} danger>
                    {t("page.edictDetail.cancelButton")}
                  </Button>
                </Popconfirm>
              </Space>
            )}
          </div>
        </GlowCard>
      )}

      {hasPendingReview && edict.status === "open" && (
        <Alert
          type="info"
          showIcon
          message={t("page.edictDetail.pendingReviewHint")}
          style={{ marginBottom: 12 }}
        />
      )}

      {!canFollowUp && edict.status === "open" && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "flex-end",
            gap: 12,
            marginBottom: 24,
          }}
        >
          {edict.acceptance &&
            edict.runtime.lifecycle_phase === "active" &&
            hasActiveMemorial && (
              <Button icon={<PauseCircleOutlined />} onClick={handlePause}>
                {t("button.pauseAfterRound")}
              </Button>
            )}
          {edict.acceptance && edict.runtime.lifecycle_phase === "paused" && (
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={handleResume}
            >
              {t("button.resume")}
            </Button>
          )}
          <Popconfirm
            title={t("page.edictDetail.confirmClose.title")}
            description={t("page.edictDetail.confirmClose.description")}
            onConfirm={handleClose}
            okText={t("common.confirm")}
            cancelText={t("common.cancel")}
          >
            <Button icon={<CheckOutlined />}>
              {t("page.edictDetail.closeButton")}
            </Button>
          </Popconfirm>
          <Popconfirm
            title={t("page.edictDetail.confirmCancel.title")}
            description={t("page.edictDetail.confirmCancel.description")}
            onConfirm={handleCancel}
            okText={t("common.confirm")}
            cancelText={t("common.cancel")}
          >
            <Button icon={<StopOutlined />} danger>
              {t("page.edictDetail.cancelButton")}
            </Button>
          </Popconfirm>
        </div>
      )}

      {events.length > 0 && <EventTimeline events={events} />}

      {edictId && edict?.acceptance && <OuterLoopTimeline edictId={edictId} />}

      {edictId && <PolicyTimeline edictId={edictId} />}

      <Modal
        title={t("page.edictDetail.editModal.title")}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={handleEditSave}
        confirmLoading={editSaving}
        okText={t("common2.save")}
        cancelText={t("common.cancel")}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <div>
            <Typography.Text strong>
              {t("page.edictDetail.editModal.fieldTitle")}
            </Typography.Text>
            <Input
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              style={{ marginTop: 8 }}
            />
          </div>
          <div>
            <Typography.Text strong>
              {t("page.edictDetail.editModal.fieldGoal")}
            </Typography.Text>
            <Input.TextArea
              value={editGoal}
              onChange={(e) => setEditGoal(e.target.value)}
              rows={3}
              autoSize={{ minRows: 2, maxRows: 6 }}
              style={{ marginTop: 8 }}
            />
          </div>
          <div>
            <Typography.Text strong>
              {t("page.edictDetail.editModal.fieldContext")}
            </Typography.Text>
            <Input.TextArea
              value={editContext}
              onChange={(e) => setEditContext(e.target.value)}
              rows={3}
              autoSize={{ minRows: 2, maxRows: 6 }}
              placeholder={t("common2.optional")}
              style={{ marginTop: 8 }}
            />
          </div>
        </Space>
      </Modal>
    </PageContainer>
  );
}
