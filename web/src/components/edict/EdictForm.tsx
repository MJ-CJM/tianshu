import { useState } from "react";
import { Alert, Form, Input, Button, Collapse, Select, Radio, Switch, Space } from "antd";
import { SendOutlined } from "@ant-design/icons";
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
import type { PolicyProfileValue } from "../policy/PolicyProfilePanel";
import RuntimeConfigSection from "./RuntimeConfigSection";
import AcceptanceConfigSection from "./AcceptanceConfigSection";

interface EdictFormProps {
  onSubmit: (values: EdictCreateRequest) => void;
  loading: boolean;
}

export default function EdictForm({ onSubmit, loading }: EdictFormProps) {
  const t = useT();
  const [form] = Form.useForm();
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
      form.setFieldsValue(patch);
      if (draft.context || draft.priority) {
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
    const executor = values.executor as string | undefined;
    if (executor && executor !== "native") {
      runtime.executor = executor;
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
        priority: "normal",
        review_policy: "always",
      }}
      style={{ maxWidth: 640 }}
    >
      <Collapse
        ghost
        style={{ marginBottom: 8 }}
        items={[
          {
            key: "nl",
            label: t("form.edict.field.nlLabel"),
            children: (
              <>
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
              </>
            ),
          },
        ]}
      />

      <Form.Item name="title" label={t("form.edict.field.title")}>
        <Input placeholder={t("form.edict.placeholder.title")} />
      </Form.Item>

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
                  name="executor"
                  label={t("form.edict.field.executor")}
                  tooltip={t("form.edict.tooltip.executor")}
                >
                  <Select
                    options={[
                      { value: "native", label: t("executor.native") },
                      { value: "keqing:claude-code", label: t("executor.claudeCode") },
                      { value: "keqing:codex", label: t("executor.codex") },
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

                <RuntimeConfigSection
                  policyProfile={policyProfile}
                  setPolicyProfile={setPolicyProfile}
                  netState={netState}
                  setNetState={setNetState}
                />
              </>
            ),
          },
          {
            key: "long-task",
            label: t("form.edict.section.longTask"),
            children: (
              <>
                <AcceptanceConfigSection
                  longTaskEnabled={longTaskEnabled}
                  setLongTaskEnabled={setLongTaskEnabled}
                  assignMode={assignMode}
                />
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
