import { useState } from "react";
import { Alert, Form, Input, InputNumber, Button, Collapse, Select, Divider, Radio, Switch, Space, Card } from "antd";
import { SendOutlined, MinusCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { parseEdict } from "../../api/edicts";
import { usePersonas } from "../../hooks/usePersonas";
import { useT } from "../../i18n";
import type {
  AcceptanceCriteria,
  CheckSpec,
  EdictCreateRequest,
  EdictRuntime,
  ExecutionProfile,
} from "../../api/types";
import PolicyProfilePanel from "../policy/PolicyProfilePanel";
import type { PolicyProfileValue } from "../policy/PolicyProfilePanel";
import NetworkCapabilitySection from "./NetworkCapabilitySection";

interface EdictFormProps {
  onSubmit: (values: EdictCreateRequest) => void;
  loading: boolean;
}

export default function EdictForm({ onSubmit, loading }: EdictFormProps) {
  const t = useT();
  const [form] = Form.useForm();
  const [scheduleType, setScheduleType] = useState("immediate");
  const [assignMode, setAssignMode] = useState<"auto" | "direct">("auto");
  const [policyProfile, setPolicyProfile] =
    useState<PolicyProfileValue | null>(null);
  const [longTaskEnabled, setLongTaskEnabled] = useState(false);
  const [activePanels, setActivePanels] = useState<string[]>([]);
  const [nlText, setNlText] = useState("");
  const [nlLoading, setNlLoading] = useState(false);
  const [nlNotes, setNlNotes] = useState<string | null>(null);
  const [nlError, setNlError] = useState<string | null>(null);
  const [netState, setNetState] = useState<{
    api_request_hosts: string[];
    api_request_write_hosts: string[];
  }>({ api_request_hosts: [], api_request_write_hosts: [] });
  const { data: personas } = usePersonas();

  const personaOptions = (personas ?? []).map((p) => ({
    value: p.id,
    label: `${p.name} (${p.id})`,
  }));

  const cabinetPersonas = (personas ?? []).filter((p) => p.department === "neige");
  const plannerOptions = cabinetPersonas.map((p) => ({
    value: p.id,
    label: `${p.name}${p.llm_config_name ? ` (${p.llm_config_name})` : ""}`,
  }));

  const criticPersonas = personas ?? [];
  const criticPersonaOptions = criticPersonas.map((p) => ({
    value: p.id,
    label: `${p.name} · ${t(`dept.${p.department}`)}${p.llm_config_name ? ` (${p.llm_config_name})` : ""}`,
  }));
  const defaultCriticPersonaIds = (() => {
    const ducha = criticPersonas.find((p) => p.department === "ducha")?.id;
    if (ducha) return [ducha];
    const neige = criticPersonas.find((p) => p.department === "neige")?.id;
    if (neige) return [neige];
    return criticPersonas[0] ? [criticPersonas[0].id] : [];
  })();

  const handleSmartFill = async () => {
    if (!nlText.trim()) return;
    setNlLoading(true);
    setNlError(null);
    setNlNotes(null);
    try {
      const { draft, notes } = await parseEdict(nlText.trim());
      const patch: Record<string, unknown> = {};
      if (draft.goal) patch.goal = draft.goal;
      if (draft.title) patch.title = draft.title;
      if (draft.context) patch.context = draft.context;
      if (draft.priority) patch.priority = draft.priority;
      if (draft.schedule) {
        patch.schedule_type = draft.schedule.type;
        setScheduleType(draft.schedule.type);
        if (draft.schedule.cron) patch.cron_expr = draft.schedule.cron;
        if (draft.schedule.at) patch.schedule_at = draft.schedule.at;
      }
      form.setFieldsValue(patch);
      if (draft.title || draft.context || draft.priority) {
        setActivePanels((prev) =>
          prev.includes("advanced") ? prev : [...prev, "advanced"],
        );
      }
      if (notes) setNlNotes(notes);
    } catch {
      setNlError(t("form.edict.field.nlFailed"));
    } finally {
      setNlLoading(false);
    }
  };

  const handleFinish = (values: Record<string, unknown>) => {
    const req: EdictCreateRequest = {
      goal: values.goal as string,
      title: (values.title as string) || undefined,
      context: (values.context as string) || undefined,
    };

    const st = (values.schedule_type as string) ?? "immediate";
    if (st !== "immediate") {
      req.schedule = {
        type: st,
        ...(st === "cron" ? { cron: values.cron_expr as string } : {}),
        ...(st === "once" ? { at: values.schedule_at as string } : {}),
      };
    }

    const priority = values.priority as string | undefined;
    if (priority && priority !== "normal") {
      req.priority = priority;
    }

    const reviewPolicy = values.review_policy as string | undefined;
    if (reviewPolicy) {
      req.review_policy = reviewPolicy;
    }

    const constraints = values.constraints as string[] | undefined;
    if (constraints && constraints.length > 0) {
      req.constraints = constraints;
    }

    const outputFormat = values.output_format as string | undefined;
    if (outputFormat?.trim()) {
      req.output_format = outputFormat.trim();
    }

    const runtime: Partial<EdictRuntime> = {};
    const timeoutSeconds = values.timeout_seconds as number | undefined;
    if (timeoutSeconds !== undefined && timeoutSeconds !== 300) {
      runtime.timeout_seconds = timeoutSeconds;
    }
    const maxIterations = values.max_iterations as number | undefined;
    if (maxIterations !== undefined && maxIterations !== 20) {
      runtime.max_iterations = maxIterations;
    }
    const maxConcurrency = values.max_concurrency as number | undefined;
    if (maxConcurrency !== undefined && maxConcurrency !== 1) {
      runtime.max_concurrency = maxConcurrency;
    }
    const retryLimit = values.retry_limit as number | undefined;
    if (retryLimit !== undefined && retryLimit !== 0) {
      runtime.retry_limit = retryLimit;
    }
    const tokenBudget = values.token_budget as number | undefined;
    if (tokenBudget) {
      runtime.token_budget = tokenBudget;
    }
    const costBudget = values.cost_budget_cny as number | undefined;
    if (costBudget) {
      runtime.cost_budget_cny = costBudget;
    }
    if (
      policyProfile &&
      (policyProfile.template_name ||
        policyProfile.allowed_paths.length > 0 ||
        policyProfile.allowed_bash_prefixes.length > 0)
    ) {
      runtime.policy_profile = policyProfile;
    }
    if (netState.api_request_hosts.length > 0) {
      runtime.api_request_hosts = netState.api_request_hosts;
    }
    if (netState.api_request_write_hosts.length > 0) {
      runtime.api_request_write_hosts = netState.api_request_write_hosts;
    }
    if (Object.keys(runtime).length > 0) {
      req.runtime = runtime;
    }

    if (assignMode === "direct" && values.assigned_persona_id) {
      req.assigned_persona_id = values.assigned_persona_id as string;
    }

    if (assignMode === "auto" && values.planner_persona_id) {
      req.planner_persona_id = values.planner_persona_id as string;
    }

    if (assignMode === "auto" && values.plan_review) {
      req.plan_review = true;
    }

    if (longTaskEnabled) {
      const acceptance: AcceptanceCriteria = {};
      const maxOuter = values.max_outer_iterations as number | undefined;
      if (maxOuter !== undefined && maxOuter !== 5) {
        acceptance.max_outer_iterations = maxOuter;
      }
      const deadlineHours = (values.deadline_hours as number | undefined) ?? 0;
      const deadlineMinutes = (values.deadline_minutes as number | undefined) ?? 0;
      const deadlineSeconds = deadlineHours * 3600 + deadlineMinutes * 60;
      if (deadlineSeconds > 0) {
        acceptance.deadline_seconds = deadlineSeconds;
      }
      const onExhaustion = values.on_exhaustion as
        | "escalate"
        | "best_effort"
        | "fail"
        | undefined;
      if (onExhaustion && onExhaustion !== "escalate") {
        acceptance.on_exhaustion = onExhaustion;
      }
      const onCriticUnavail = values.on_critic_unavailable as
        | "escalate"
        | "skip"
        | undefined;
      if (onCriticUnavail && onCriticUnavail !== "skip") {
        acceptance.on_critic_unavailable = onCriticUnavail;
      }
      const sameIssueThreshold = values.same_issue_threshold as
        | number
        | undefined;
      const criticPersonaIds = values.critic_persona_ids as string[] | undefined;
      const strictness = values.critic_strictness as
        | "lenient"
        | "balanced"
        | "strict"
        | undefined;
      if (
        sameIssueThreshold !== undefined ||
        (criticPersonaIds && criticPersonaIds.length > 0) ||
        (strictness && strictness !== "lenient")
      ) {
        acceptance.critic = {
          ...(criticPersonaIds && criticPersonaIds.length > 0
            ? { persona_ids: criticPersonaIds }
            : {}),
          ...(sameIssueThreshold !== undefined && sameIssueThreshold !== 2
            ? { same_issue_threshold: sameIssueThreshold }
            : {}),
          ...(strictness && strictness !== "lenient" ? { strictness } : {}),
        };
      }
      const minOuter = values.min_outer_iterations as number | undefined;
      if (minOuter !== undefined && minOuter > 1) {
        acceptance.min_outer_iterations = minOuter;
      }
      const checksRaw = values.checks as CheckSpec[] | undefined;
      if (checksRaw && checksRaw.length > 0) {
        acceptance.checks = checksRaw.filter((c) => c?.name);
      }
      const l1Max = values.l1_max_rounds as number | undefined;
      const l2Max = values.l2_max_rounds as number | undefined;
      if (
        (l1Max !== undefined && l1Max !== 2) ||
        (l2Max !== undefined && l2Max !== 1)
      ) {
        acceptance.escalation = {
          ...(l1Max !== undefined && l1Max !== 2 ? { l1_max_rounds: l1Max } : {}),
          ...(l2Max !== undefined && l2Max !== 1 ? { l2_max_rounds: l2Max } : {}),
        };
      }
      req.acceptance = acceptance;

      const profile = values.execution_profile as ExecutionProfile | undefined;
      if (profile && profile !== "foreground") {
        req.execution_profile = profile;
      }
    }

    onSubmit(req);
  };

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={handleFinish}
      requiredMark={false}
      initialValues={{
        schedule_type: "immediate",
        priority: "normal",
        review_policy: "always",
      }}
      style={{ maxWidth: 640 }}
    >
      <Form.Item label={t("form.edict.field.nlLabel")}>
        <Space.Compact style={{ width: "100%" }}>
          <Input.TextArea
            rows={2}
            value={nlText}
            onChange={(e) => setNlText(e.target.value)}
            placeholder={t("form.edict.field.nlPlaceholder")}
            style={{ resize: "vertical" }}
          />
          <Button type="primary" loading={nlLoading} onClick={handleSmartFill}>
            {t("form.edict.field.nlButton")}
          </Button>
        </Space.Compact>
        {nlNotes && (
          <Alert type="info" showIcon style={{ marginTop: 8 }} message={nlNotes} />
        )}
        {nlError && (
          <Alert type="warning" showIcon style={{ marginTop: 8 }} message={nlError} />
        )}
      </Form.Item>
      <Divider style={{ margin: "8px 0 16px" }} />
      <Form.Item
        name="goal"
        label={t("form.edict.field.goal")}
        rules={[{ required: true, message: t("form.edict.validation.goalRequired") }]}
      >
        <Input.TextArea
          rows={4}
          placeholder={t("form.edict.placeholder.goal")}
          style={{ resize: "vertical" }}
        />
      </Form.Item>

      <Form.Item name="schedule_type" label={t("form.edict.field.scheduleType")}>
        <Select
          onChange={(v) => setScheduleType(v)}
          options={[
            { value: "immediate", label: t("form.edict.option.scheduleImmediate") },
            { value: "once", label: t("form.edict.option.scheduleOnce") },
            { value: "cron", label: t("form.edict.option.scheduleCron") },
          ]}
        />
      </Form.Item>
      {scheduleType === "cron" && (
        <Form.Item
          name="cron_expr"
          label={t("form.edict.field.cronExpr")}
          rules={[{ required: true, message: t("form.edict.validation.cronExprRequired") }]}
        >
          <Input placeholder={t("form.edict.placeholder.cronExpr")} />
        </Form.Item>
      )}
      {scheduleType === "once" && (
        <Form.Item
          name="schedule_at"
          label={t("form.edict.field.scheduleAt")}
          rules={[{ required: true, message: t("form.edict.validation.scheduleAtRequired") }]}
        >
          <Input placeholder={t("form.edict.placeholder.scheduleAt")} />
        </Form.Item>
      )}

      <Collapse
        ghost
        style={{ marginBottom: 24 }}
        activeKey={activePanels}
        onChange={(keys) => setActivePanels(keys as string[])}
        items={[
          {
            key: "advanced",
            label: t("form.edict.section.more"),
            children: (
              <>
                <Form.Item name="title" label={t("form.edict.field.title")}>
                  <Input placeholder={t("form.edict.placeholder.title")} />
                </Form.Item>

                <Form.Item label={t("form.edict.field.executionMode")}>
                  <Radio.Group
                    value={assignMode}
                    onChange={(e) => {
                      setAssignMode(e.target.value);
                      if (e.target.value === "auto") {
                        form.setFieldValue("assigned_persona_id", undefined);
                      }
                    }}
                  >
                    <Radio value="auto">{t("form.edict.option.autoPlanning")}</Radio>
                    <Radio value="direct">{t("form.edict.option.directAssign")}</Radio>
                  </Radio.Group>
                </Form.Item>

                {assignMode === "direct" && (
                  <Form.Item
                    name="assigned_persona_id"
                    rules={[{ required: true, message: t("form.edict.validation.assignedPersonaRequired") }]}
                  >
                    <Select
                      placeholder={t("form.edict.placeholder.assignedPersona")}
                      options={personaOptions}
                      showSearch
                      optionFilterProp="label"
                    />
                  </Form.Item>
                )}

                {assignMode === "auto" && cabinetPersonas.length > 1 && (
                  <Form.Item
                    name="planner_persona_id"
                    label={t("form.edict.field.plannerPersona")}
                    tooltip={t("form.edict.tooltip.plannerPersona")}
                  >
                    <Select
                      placeholder={t("form.edict.placeholder.plannerPersona")}
                      options={plannerOptions}
                      allowClear
                      showSearch
                      optionFilterProp="label"
                    />
                  </Form.Item>
                )}

                {assignMode === "auto" && (
                  <Form.Item
                    name="plan_review"
                    label={t("form.edict.field.planReview")}
                    valuePropName="checked"
                    tooltip={t("form.edict.tooltip.planReview")}
                  >
                    <Switch />
                  </Form.Item>
                )}

                <Form.Item name="context" label={t("form.edict.field.context")}>
                  <Input.TextArea
                    rows={3}
                    placeholder={t("form.edict.placeholder.context")}
                    style={{ resize: "vertical" }}
                  />
                </Form.Item>

                <Form.Item
                  name="priority"
                  label={t("form.edict.field.priority")}
                >
                  <Select
                    options={[
                      { value: "urgent", label: t("priority.urgent") },
                      { value: "normal", label: t("priority.normal") },
                      { value: "low", label: t("priority.low") },
                    ]}
                  />
                </Form.Item>

                <Form.Item
                  name="review_policy"
                  label={t("form.edict.field.reviewPolicy")}
                >
                  <Select
                    options={[
                      { value: "always", label: t("reviewPolicy.always") },
                      { value: "on_flag", label: t("reviewPolicy.on_flag") },
                      { value: "on_failure", label: t("reviewPolicy.on_failure") },
                      { value: "never", label: t("reviewPolicy.never") },
                    ]}
                  />
                </Form.Item>

                <Form.Item
                  name="constraints"
                  label={t("form.edict.field.constraints")}
                >
                  <Select
                    mode="tags"
                    placeholder={t("form.edict.placeholder.constraints")}
                    tokenSeparators={[","]}
                  />
                </Form.Item>

                <Form.Item
                  name="output_format"
                  label={t("form.edict.field.outputFormat")}
                >
                  <Input.TextArea
                    rows={2}
                    placeholder={t("form.edict.placeholder.outputFormat")}
                    style={{ resize: "vertical" }}
                  />
                </Form.Item>

                <Divider style={{ margin: "12px 0" }} />
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>
                  {t("form.edict.section.runtime")}
                </div>

                <Form.Item name="timeout_seconds" label={t("form.edict.field.timeoutSeconds")}>
                  <InputNumber min={10} max={3600} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.timeoutSeconds")} />
                </Form.Item>

                <Form.Item name="max_iterations" label={t("form.edict.field.maxIterations")}>
                  <InputNumber min={1} max={200} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.maxIterations")} />
                </Form.Item>

                <Form.Item name="max_concurrency" label={t("form.edict.field.maxConcurrency")}>
                  <InputNumber min={1} max={8} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.maxConcurrency")} />
                </Form.Item>

                <Form.Item name="retry_limit" label={t("form.edict.field.retryLimit")}>
                  <InputNumber min={0} max={10} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.retryLimit")} />
                </Form.Item>

                <Form.Item name="token_budget" label={t("form.edict.field.tokenBudget")}>
                  <InputNumber min={1} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.tokenBudget")} />
                </Form.Item>

                <Form.Item name="cost_budget_cny" label={t("form.edict.field.costBudget")}>
                  <InputNumber min={0} step={0.01} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.costBudget")} />
                </Form.Item>

                <Divider style={{ margin: "12px 0" }} />
                <PolicyProfilePanel
                  value={policyProfile ?? undefined}
                  onChange={setPolicyProfile}
                />

                <NetworkCapabilitySection
                  profileTemplate={policyProfile?.template_name ?? null}
                  apiRequestHosts={netState.api_request_hosts}
                  apiRequestWriteHosts={netState.api_request_write_hosts}
                  onChange={(patch) =>
                    setNetState((prev) => ({ ...prev, ...patch }))
                  }
                />
              </>
            ),
          },
          {
            key: "long-task",
            label: t("form.edict.section.longTask"),
            children: (
              <>
                <Form.Item label={t("form.edict.field.longTaskEnabled")} tooltip={t("form.edict.tooltip.longTaskEnabled")}>
                  <Switch
                    checked={longTaskEnabled}
                    onChange={setLongTaskEnabled}
                    checkedChildren={t("common2.enabled")}
                    unCheckedChildren={t("common2.disabled")}
                  />
                </Form.Item>

                {longTaskEnabled && (
                  <>
                    <Form.Item label={t("form.edict.field.templatePreset")} tooltip={t("form.edict.tooltip.templatePreset")}>
                      <Space wrap>
                        <Button
                          size="small"
                          onClick={() => {
                            form.setFieldsValue({
                              execution_profile: "foreground",
                              max_outer_iterations: 5,
                              on_exhaustion: "escalate",
                              on_critic_unavailable: "skip",
                              same_issue_threshold: 2,
                              critic_persona_ids: defaultCriticPersonaIds,
                            });
                          }}
                        >
                          {t("form.edict.template.analysis")}
                        </Button>
                        <Button
                          size="small"
                          onClick={() => {
                            form.setFieldsValue({
                              execution_profile: "foreground",
                              max_outer_iterations: 10,
                              min_outer_iterations: 3,
                              critic_strictness: "balanced",
                              on_exhaustion: "best_effort",
                              on_critic_unavailable: "skip",
                              same_issue_threshold: 3,
                              critic_persona_ids: defaultCriticPersonaIds,
                            });
                          }}
                        >
                          {t("form.edict.template.creative")}
                        </Button>
                        <Button
                          size="small"
                          onClick={() => {
                            form.setFieldsValue({
                              execution_profile: "checkpointed",
                              max_outer_iterations: 8,
                              deadline_hours: 1,
                              deadline_minutes: 0,
                              on_exhaustion: "escalate",
                              on_critic_unavailable: "escalate",
                              same_issue_threshold: 2,
                              critic_persona_ids: defaultCriticPersonaIds,
                            });
                          }}
                        >
                          {t("form.edict.template.coding")}
                        </Button>
                        <Button
                          size="small"
                          onClick={() => {
                            form.setFieldsValue({
                              execution_profile: "background",
                              max_outer_iterations: 15,
                              min_outer_iterations: 4,
                              critic_strictness: "strict",
                              deadline_hours: 2,
                              deadline_minutes: 0,
                              on_exhaustion: "best_effort",
                              on_critic_unavailable: "skip",
                              same_issue_threshold: 3,
                              critic_persona_ids: defaultCriticPersonaIds,
                            });
                          }}
                        >
                          {t("form.edict.template.research")}
                        </Button>
                      </Space>
                    </Form.Item>

                    <Form.Item
                      name="execution_profile"
                      label={t("form.edict.field.executionProfile")}
                      tooltip={t("form.edict.tooltip.executionProfile")}
                      initialValue="foreground"
                    >
                      <Radio.Group>
                        <Radio value="foreground">{t("executionProfile.foreground")}</Radio>
                        <Radio value="checkpointed">{t("executionProfile.checkpointed")}</Radio>
                        <Radio value="background">{t("executionProfile.background")}</Radio>
                      </Radio.Group>
                    </Form.Item>

                    <Form.Item
                      name="max_outer_iterations"
                      label={t("form.edict.field.maxOuterIterations")}
                      tooltip={t("form.edict.tooltip.maxOuterIterations")}
                    >
                      <InputNumber min={1} max={50} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.maxOuterIterations")} />
                    </Form.Item>

                    <Form.Item
                      name="min_outer_iterations"
                      label={t("form.edict.field.minOuterIterations")}
                      tooltip={t("form.edict.tooltip.minOuterIterations")}
                    >
                      <InputNumber min={1} max={20} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.minOuterIterations")} />
                    </Form.Item>

                    <Form.Item
                      name="critic_strictness"
                      label={t("form.edict.field.criticStrictness")}
                      tooltip={t("form.edict.tooltip.criticStrictness")}
                      initialValue="lenient"
                    >
                      <Radio.Group>
                        <Radio value="lenient">{t("strictness.lenient")}</Radio>
                        <Radio value="balanced">{t("strictness.balanced")}</Radio>
                        <Radio value="strict">{t("strictness.strict")}</Radio>
                      </Radio.Group>
                    </Form.Item>

                    <Form.Item
                      label={t("form.edict.field.deadline")}
                      tooltip={t("form.edict.tooltip.deadline")}
                    >
                      <Space>
                        <Form.Item name="deadline_hours" noStyle>
                          <InputNumber
                            min={0}
                            max={48}
                            placeholder={t("form.edict.placeholder.deadlineHours")}
                            addonAfter={t("unit.hours")}
                            style={{ width: 130 }}
                          />
                        </Form.Item>
                        <Form.Item name="deadline_minutes" noStyle>
                          <InputNumber
                            min={0}
                            max={59}
                            placeholder={t("form.edict.placeholder.deadlineMinutes")}
                            addonAfter={t("unit.minutes")}
                            style={{ width: 130 }}
                          />
                        </Form.Item>
                      </Space>
                    </Form.Item>

                    <Form.Item
                      name="on_exhaustion"
                      label={t("form.edict.field.onExhaustion")}
                      tooltip={t("form.edict.tooltip.onExhaustion")}
                      initialValue="escalate"
                    >
                      <Radio.Group>
                        <Radio value="escalate">{t("exhaustion.escalate")}</Radio>
                        <Radio value="best_effort">{t("exhaustion.best_effort")}</Radio>
                        <Radio value="fail">{t("exhaustion.fail")}</Radio>
                      </Radio.Group>
                    </Form.Item>

                    <Form.Item
                      name="on_critic_unavailable"
                      label={t("form.edict.field.onCriticUnavailable")}
                      tooltip={t("form.edict.tooltip.onCriticUnavailable")}
                      initialValue="skip"
                    >
                      <Radio.Group>
                        <Radio value="skip">{t("criticUnavail.skip")}</Radio>
                        <Radio value="escalate">{t("criticUnavail.escalate")}</Radio>
                      </Radio.Group>
                    </Form.Item>

                    <Form.Item
                      name="critic_persona_ids"
                      label={t("form.edict.field.criticPersonas")}
                      tooltip={t("form.edict.tooltip.criticPersonas")}
                      rules={[{
                        required: true,
                        type: "array",
                        min: 1,
                        message: t("form.edict.validation.criticPersonasRequired"),
                      }]}
                      initialValue={defaultCriticPersonaIds}
                    >
                      <Select
                        mode="multiple"
                        options={criticPersonaOptions}
                        showSearch
                        optionFilterProp="label"
                        placeholder={t("form.edict.placeholder.criticPersonas")}
                      />
                    </Form.Item>

                    <Form.Item
                      noStyle
                      shouldUpdate={(p, c) =>
                        p.assigned_persona_id !== c.assigned_persona_id ||
                        p.critic_persona_ids !== c.critic_persona_ids
                      }
                    >
                      {({ getFieldValue }) => {
                        if (assignMode !== "direct") return null;
                        const exec = getFieldValue("assigned_persona_id") as string | undefined;
                        const critics = (getFieldValue("critic_persona_ids") as string[] | undefined) ?? [];
                        if (!exec || !critics.includes(exec)) return null;
                        return (
                          <Alert
                            type="warning"
                            showIcon
                            style={{ marginBottom: 16 }}
                            message={t("form.edict.warning.criticSelfReviewTitle")}
                            description={t("form.edict.warning.criticSelfReviewDesc")}
                          />
                        );
                      }}
                    </Form.Item>

                    <Form.Item
                      name="same_issue_threshold"
                      label={t("form.edict.field.sameIssueThreshold")}
                      tooltip={t("form.edict.tooltip.sameIssueThreshold")}
                    >
                      <InputNumber min={1} max={10} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.sameIssueThreshold")} />
                    </Form.Item>

                    <Form.Item name="l1_max_rounds" label={t("form.edict.field.l1MaxRounds")}>
                      <InputNumber min={1} max={5} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.l1MaxRounds")} />
                    </Form.Item>

                    <Form.Item name="l2_max_rounds" label={t("form.edict.field.l2MaxRounds")}>
                      <InputNumber min={1} max={5} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.l2MaxRounds")} />
                    </Form.Item>

                    <Divider style={{ margin: "12px 0" }} />
                    <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>
                      {t("form.edict.section.checks")}
                    </div>

                    <Form.List name="checks">
                      {(fields, { add, remove }) => (
                        <>
                          {fields.map(({ key, name }) => (
                            <Card
                              key={key}
                              size="small"
                              style={{ marginBottom: 12 }}
                              extra={
                                <Button
                                  type="text"
                                  danger
                                  size="small"
                                  icon={<MinusCircleOutlined />}
                                  onClick={() => remove(name)}
                                />
                              }
                            >
                              <Form.Item
                                name={[name, "name"]}
                                label={t("form.check.field.name")}
                                rules={[{ required: true, message: t("form.check.validation.nameRequired") }]}
                              >
                                <Input placeholder={t("form.check.placeholder.name")} />
                              </Form.Item>
                              <Form.Item
                                name={[name, "kind"]}
                                label={t("form.check.field.kind")}
                                initialValue="bash"
                              >
                                <Radio.Group>
                                  <Radio value="bash">{t("checkKind.bash")}</Radio>
                                  <Radio value="lint">{t("checkKind.lint")}</Radio>
                                  <Radio value="rubric">{t("checkKind.rubric")}</Radio>
                                </Radio.Group>
                              </Form.Item>
                              <Form.Item
                                noStyle
                                shouldUpdate={(prev, cur) =>
                                  prev?.checks?.[name]?.kind !== cur?.checks?.[name]?.kind
                                }
                              >
                                {({ getFieldValue }) => {
                                  const kind = getFieldValue(["checks", name, "kind"]);
                                  if (kind === "rubric") {
                                    return (
                                      <>
                                        <Form.Item
                                          name={[name, "rubric"]}
                                          label={t("form.check.field.rubric")}
                                          rules={[{ required: true, message: t("form.check.validation.rubricRequired") }]}
                                        >
                                          <Input.TextArea
                                            rows={3}
                                            placeholder={t("form.check.placeholder.rubric")}
                                          />
                                        </Form.Item>
                                        <Form.Item
                                          name={[name, "pass_threshold"]}
                                          label={t("form.check.field.passThreshold")}
                                          initialValue={0.8}
                                        >
                                          <InputNumber
                                            min={0}
                                            max={1}
                                            step={0.05}
                                            style={{ width: "100%" }}
                                          />
                                        </Form.Item>
                                      </>
                                    );
                                  }
                                  return (
                                    <Form.Item
                                      name={[name, "command"]}
                                      label={t("form.check.field.command")}
                                      rules={[{ required: true, message: t("form.check.validation.commandRequired") }]}
                                    >
                                      <Input placeholder={t("form.check.placeholder.command")} />
                                    </Form.Item>
                                  );
                                }}
                              </Form.Item>
                              <Form.Item
                                name={[name, "timeout_seconds"]}
                                label={t("form.check.field.timeout")}
                                initialValue={60}
                              >
                                <InputNumber min={1} max={3600} style={{ width: "100%" }} />
                              </Form.Item>
                            </Card>
                          ))}
                          <Space>
                            <Button
                              type="dashed"
                              icon={<PlusOutlined />}
                              onClick={() =>
                                add({ kind: "bash", name: "", timeout_seconds: 60 })
                              }
                            >
                              {t("form.check.action.addBashLint")}
                            </Button>
                            <Button
                              type="dashed"
                              icon={<PlusOutlined />}
                              onClick={() =>
                                add({ kind: "rubric", name: "", pass_threshold: 0.8 })
                              }
                            >
                              {t("form.check.action.addRubric")}
                            </Button>
                          </Space>
                        </>
                      )}
                    </Form.List>
                  </>
                )}
              </>
            ),
          },
        ]}
      />

      <Form.Item>
        <Button
          type="primary"
          htmlType="submit"
          loading={loading}
          icon={<SendOutlined />}
          size="large"
        >
          {t("nav.edictCreate")}
        </Button>
      </Form.Item>
    </Form>
  );
}
