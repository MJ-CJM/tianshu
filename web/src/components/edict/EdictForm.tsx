import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import { SendOutlined, ToolOutlined } from "@ant-design/icons";
import { parseEdict, previewEdictGovernance } from "../../api/edicts";
import { usePersonas } from "../../hooks/usePersonas";
import { useT } from "../../i18n";
import type {
  AcceptanceCriteria,
  CheckSpec,
  EdictCreateRequest,
  EdictRuntime,
  ExecutionProfile,
  GovernanceContractPreview,
} from "../../api/types";
import type { PolicyProfileValue } from "../policy/PolicyProfilePanel";
import PolicyProfilePanel from "../policy/PolicyProfilePanel";
import NetworkCapabilitySection from "./NetworkCapabilitySection";
import AcceptanceConfigSection from "./AcceptanceConfigSection";
import { EDICT_PRESETS, getPreset } from "./edictPresets";

interface EdictFormProps {
  onSubmit: (values: EdictCreateRequest) => void;
  loading: boolean;
  governanceConfirmation?: "advisory" | "always";
}

interface PendingGovernanceDispatch {
  request: EdictCreateRequest;
  preview: GovernanceContractPreview;
}

export default function EdictForm({
  onSubmit,
  loading,
  governanceConfirmation = "advisory",
}: EdictFormProps) {
  const t = useT();
  const [form] = Form.useForm();
  const [assignMode, setAssignMode] = useState<"auto" | "direct">("auto");
  const [policyProfile, setPolicyProfile] = useState<PolicyProfileValue | null>(null);
  const [longTaskEnabled, setLongTaskEnabled] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);
  const [expertMode, setExpertMode] = useState(false);
  const [nlText, setNlText] = useState("");
  const [nlLoading, setNlLoading] = useState(false);
  const [nlNotes, setNlNotes] = useState<string | null>(null);
  const [nlError, setNlError] = useState<string | null>(null);
  const [governanceError, setGovernanceError] = useState<string | null>(null);
  const [governanceAdvisory, setGovernanceAdvisory] = useState<string | null>(null);
  const [pendingGovernanceDispatch, setPendingGovernanceDispatch] =
    useState<PendingGovernanceDispatch | null>(null);
  const [governancePreviewLoading, setGovernancePreviewLoading] = useState(false);
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

  const applyPreset = (key: string) => {
    const preset = getPreset(key);
    if (!preset) return;
    setSelectedPreset(key);
    setLongTaskEnabled(preset.longTask);
    if (preset.assignMode) setAssignMode(preset.assignMode);
    form.setFieldsValue(preset.fields);
  };

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
      if (notes) setNlNotes(notes);
    } catch {
      setNlError(t("form.edict.field.nlFailed"));
    } finally {
      setNlLoading(false);
    }
  };

  const handleFinish = async (values: Record<string, unknown>) => {
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
      const executorModel = (values.executor_model as string | undefined)?.trim();
      if (executorModel) {
        runtime.executor_model = executorModel;
      }
    }
    if (values.conversation) {
      runtime.conversation = true;
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
      const onExhaustion = values.on_exhaustion as "escalate" | "best_effort" | "fail" | undefined;
      if (onExhaustion && onExhaustion !== "escalate") {
        acceptance.on_exhaustion = onExhaustion;
      }
      const onCriticUnavail = values.on_critic_unavailable as "escalate" | "skip" | undefined;
      if (onCriticUnavail && onCriticUnavail !== "skip") {
        acceptance.on_critic_unavailable = onCriticUnavail;
      }
      const sameIssueThreshold = values.same_issue_threshold as number | undefined;
      const criticPersonaIds = values.critic_persona_ids as string[] | undefined;
      const strictness = values.critic_strictness as "lenient" | "balanced" | "strict" | undefined;
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
      if ((l1Max !== undefined && l1Max !== 2) || (l2Max !== undefined && l2Max !== 1)) {
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

    setGovernanceError(null);
    setGovernanceAdvisory(null);
    setPendingGovernanceDispatch(null);
    setGovernancePreviewLoading(true);
    try {
      const preview = await previewEdictGovernance(req);
      if (!preview.compatible) {
        const incompatible =
          preview.mandatory_mismatches.map((item) => item.capability).join(", ") ||
          preview.execution_mode_mismatches
            .map((item) => `${item.adapter_id}:${item.requested_mode}`)
            .join(", ");
        setGovernanceError(
          t("form.edict.warning.governanceBlocked", {
            capabilities: incompatible,
          }),
        );
        return;
      }
      if (!preview.effective_contract) {
        setGovernanceError(t("form.edict.warning.governanceEffectiveMissing"));
        return;
      }
      if (governanceConfirmation === "always" || preview.advisory_gaps.length > 0) {
        setGovernanceAdvisory(
          preview.advisory_gaps.length > 0
            ? t("form.edict.warning.governanceAdvisory", {
                capabilities: preview.advisory_gaps.join(", "),
              })
            : t("form.edict.warning.governanceReview"),
        );
        setPendingGovernanceDispatch({
          request: { ...req, governance_contract: preview.requested_contract },
          preview,
        });
        return;
      }
      onSubmit({ ...req, governance_contract: preview.requested_contract });
    } catch {
      setGovernanceError(t("form.edict.warning.governanceUnavailable"));
    } finally {
      setGovernancePreviewLoading(false);
    }
  };

  const confirmGovernanceDispatch = () => {
    if (!pendingGovernanceDispatch) return;
    onSubmit(pendingGovernanceDispatch.request);
    setPendingGovernanceDispatch(null);
    setGovernanceAdvisory(null);
  };

  const reviseGovernanceDispatch = () => {
    setPendingGovernanceDispatch(null);
    setGovernanceAdvisory(null);
  };

  const effectiveContract = pendingGovernanceDispatch?.preview.effective_contract;
  const effectiveControlCounts = effectiveContract?.effective_controls.reduce(
    (counts, control) => ({ ...counts, [control.state]: counts[control.state] + 1 }),
    { enforced: 0, best_effort: 0, observed: 0, unsupported: 0 },
  );
  const requestedContract = pendingGovernanceDispatch?.preview.requested_contract;
  const requestedExecutor = requestedContract?.executor as Record<string, unknown> | undefined;
  const requestedWorkspace = requestedContract?.workspace as Record<string, unknown> | undefined;
  const requestedNetwork = requestedContract?.network as Record<string, unknown> | undefined;
  const requestedPermissions = requestedContract?.permissions as Record<string, unknown> | undefined;
  const requestedCapabilities = requestedContract?.capabilities as Record<string, unknown> | undefined;
  const requestedBudget = requestedContract?.budget as Record<string, unknown> | undefined;
  const requestedAcceptance = requestedContract?.acceptance as Record<string, unknown> | undefined;
  const requestedRecovery = requestedContract?.recovery as Record<string, unknown> | undefined;
  const effectiveWorkspace = effectiveContract?.workspace as Record<string, unknown> | undefined;
  const effectiveNetwork = effectiveContract?.network as Record<string, unknown> | undefined;
  const effectivePermissions = effectiveContract?.permissions as Record<string, unknown> | undefined;
  const effectiveBudget = effectiveContract?.budget as Record<string, unknown> | undefined;
  const effectiveAcceptance = effectiveContract?.acceptance as Record<string, unknown> | undefined;
  const effectiveRecovery = effectiveContract?.recovery as Record<string, unknown> | undefined;

  const capabilityNames = (value: unknown): string[] =>
    Array.isArray(value)
      ? value.map((item) =>
          typeof item === "object" && item !== null && "name" in item
            ? String((item as { name: unknown }).name)
            : String(item),
        )
      : [];

  const contractValue = (value: unknown): string => {
    if (value === null || value === undefined || value === "") return "—";
    if (Array.isArray(value)) {
      if (value.length === 0) return "—";
      return value
        .map((item) =>
          typeof item === "object" && item !== null && typeof item.name === "string"
            ? item.name
            : String(item),
        )
        .join(", ");
    }
    return String(value);
  };

  const executionGroup = (
    <>
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

      <Row gutter={16}>
        <Col xs={24} sm={12}>
          <Form.Item name="executor" label={t("form.edict.field.executor")} tooltip={t("form.edict.tooltip.executor")}>
            <Select
              options={[
                { value: "native", label: t("executor.native") },
                { value: "keqing:pi", label: t("executor.pi") },
                { value: "keqing:claude-code", label: t("executor.claudeCode") },
                { value: "keqing:codex", label: t("executor.codex") },
                { value: "keqing:opencode", label: t("executor.opencode") },
              ]}
            />
          </Form.Item>
        </Col>
        <Col xs={24} sm={12}>
          <Form.Item
            noStyle
            shouldUpdate={(prev, cur) => prev.executor !== cur.executor}
          >
            {({ getFieldValue }) =>
              String(getFieldValue("executor") ?? "").startsWith("keqing:") ? (
                <Form.Item
                  name="executor_model"
                  label={t("form.edict.field.executorModel")}
                  tooltip={t("form.edict.tooltip.executorModel")}
                >
                  <Input placeholder="anthropic/claude-opus-4-5:high" allowClear />
                </Form.Item>
              ) : null
            }
          </Form.Item>
        </Col>
      </Row>

      <Form.Item
        name="conversation"
        valuePropName="checked"
        label={t("form.edict.field.conversation")}
        tooltip={t("form.edict.tooltip.conversation")}
      >
        <Switch size="small" />
      </Form.Item>

      <Row gutter={16}>
        <Col xs={24} sm={12}>
          <Form.Item name="review_policy" label={t("form.edict.field.reviewPolicy")}>
            <Select
              options={[
                { value: "always", label: t("reviewPolicy.always") },
                { value: "on_flag", label: t("reviewPolicy.on_flag") },
                { value: "on_failure", label: t("reviewPolicy.on_failure") },
                { value: "never", label: t("reviewPolicy.never") },
              ]}
            />
          </Form.Item>
        </Col>
        <Col xs={24} sm={12}>
          <Form.Item name="priority" label={t("form.edict.field.priority")}>
            <Select
              options={[
                { value: "urgent", label: t("priority.urgent") },
                { value: "normal", label: t("priority.normal") },
                { value: "low", label: t("priority.low") },
              ]}
            />
          </Form.Item>
        </Col>
      </Row>

      <Form.Item name="context" label={t("form.edict.field.context")}>
        <Input.TextArea rows={3} placeholder={t("form.edict.placeholder.context")} style={{ resize: "vertical" }} />
      </Form.Item>

      <Form.Item name="constraints" label={t("form.edict.field.constraints")}>
        <Select mode="tags" placeholder={t("form.edict.placeholder.constraints")} tokenSeparators={[","]} />
      </Form.Item>

      <Form.Item name="output_format" label={t("form.edict.field.outputFormat")}>
        <Input.TextArea rows={2} placeholder={t("form.edict.placeholder.outputFormat")} style={{ resize: "vertical" }} />
      </Form.Item>

      <Divider style={{ margin: "12px 0" }} />

      <Row gutter={16}>
        <Col xs={24} sm={12}>
          <Form.Item name="timeout_seconds" label={t("form.edict.field.timeoutSeconds")}>
            <InputNumber min={10} max={3600} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.timeoutSeconds")} />
          </Form.Item>
        </Col>
        <Col xs={24} sm={12}>
          <Form.Item name="max_iterations" label={t("form.edict.field.maxIterations")}>
            <InputNumber min={1} max={200} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.maxIterations")} />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col xs={24} sm={12}>
          <Form.Item name="max_concurrency" label={t("form.edict.field.maxConcurrency")}>
            <InputNumber min={1} max={8} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.maxConcurrency")} />
          </Form.Item>
        </Col>
        <Col xs={24} sm={12}>
          <Form.Item name="retry_limit" label={t("form.edict.field.retryLimit")}>
            <InputNumber min={0} max={10} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.retryLimit")} />
          </Form.Item>
        </Col>
      </Row>
    </>
  );

  const budgetGroup = (
    <Row gutter={16}>
      <Col xs={24} sm={12}>
        <Form.Item name="cost_budget_cny" label={t("form.edict.field.costBudget")}>
          <InputNumber min={0} step={0.01} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.costBudget")} />
        </Form.Item>
      </Col>
      <Col xs={24} sm={12}>
        <Form.Item name="token_budget" label={t("form.edict.field.tokenBudget")}>
          <InputNumber min={1} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.tokenBudget")} />
        </Form.Item>
      </Col>
    </Row>
  );

  const permissionGroup = (
    <>
      <PolicyProfilePanel value={policyProfile ?? undefined} onChange={setPolicyProfile} />
      <NetworkCapabilitySection
        profileTemplate={policyProfile?.template_name ?? null}
        apiRequestHosts={netState.api_request_hosts}
        apiRequestWriteHosts={netState.api_request_write_hosts}
        onChange={(patch) => setNetState((prev) => ({ ...prev, ...patch }))}
      />
    </>
  );

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={handleFinish}
      requiredMark={false}
      initialValues={{ priority: "normal", review_policy: "on_failure", conversation: true }}
    >
      {/* --- 第一层:极简默认(标题 / 目标 / 智能填充) --- */}
      <Form.Item name="title" label={t("form.edict.field.title")}>
        <Input placeholder={t("form.edict.placeholder.title")} />
      </Form.Item>

      <Form.Item
        name="goal"
        label={t("form.edict.field.goal")}
        rules={[{ required: true, message: t("form.edict.validation.goalRequired") }]}
      >
        <Input.TextArea rows={4} placeholder={t("form.edict.placeholder.goal")} style={{ resize: "vertical" }} />
      </Form.Item>

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
                {nlNotes && <Alert type="info" showIcon style={{ marginTop: 8 }} message={nlNotes} />}
                {nlError && <Alert type="warning" showIcon style={{ marginTop: 8 }} message={nlError} />}
              </>
            ),
          },
        ]}
      />

      {/* --- 第二层:意图预设卡 --- */}
      <Form.Item label={t("form.edict.field.taskType")}>
        <Space wrap>
          {EDICT_PRESETS.map((p) => (
            <Card
              key={p.key}
              size="small"
              hoverable
              onClick={() => applyPreset(p.key)}
              styles={{ body: { padding: "8px 14px" } }}
              style={{
                cursor: "pointer",
                borderColor: selectedPreset === p.key ? "var(--ts-color-accent)" : undefined,
                borderWidth: selectedPreset === p.key ? 2 : 1,
                minWidth: 120,
              }}
            >
              <div style={{ fontWeight: 500 }}>
                {p.icon} {t(`preset.${p.key}.label`)}
              </div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t(`preset.${p.key}.summary`)}
              </Typography.Text>
            </Card>
          ))}
        </Space>
      </Form.Item>

      {/* --- 第三层:专家模式(默认收起) --- */}
      <Form.Item>
        <Space>
          <Switch
            checked={expertMode}
            onChange={setExpertMode}
            checkedChildren={<ToolOutlined />}
            unCheckedChildren={<ToolOutlined />}
          />
          <span>{t("form.edict.expertMode")}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("form.edict.expertHint")}
          </Typography.Text>
        </Space>
      </Form.Item>

      {expertMode && (
        <Collapse
          ghost
          defaultActiveKey={["execution"]}
          style={{ marginBottom: 16 }}
          items={[
            { key: "execution", label: t("form.edict.group.execution"), children: executionGroup },
            { key: "budget", label: t("form.edict.group.budget"), children: budgetGroup },
            { key: "permission", label: t("form.edict.group.permission"), children: permissionGroup },
            {
              key: "acceptance",
              label: t("form.edict.group.acceptance"),
              children: (
                <AcceptanceConfigSection
                  longTaskEnabled={longTaskEnabled}
                  setLongTaskEnabled={setLongTaskEnabled}
                  assignMode={assignMode}
                />
              ),
            },
          ]}
        />
      )}

      {governanceError && <Alert type="error" showIcon message={governanceError} />}
      {governanceAdvisory && pendingGovernanceDispatch && (
        <Alert
          type="warning"
          showIcon
          message={governanceAdvisory}
          description={
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              {governanceConfirmation !== "always" ? (
                <Typography.Text strong>
                  {t("form.edict.warning.governanceEffectiveTitle")}
                </Typography.Text>
              ) : null}
              <div
                style={{
                  display: "grid",
                  // 响应式:容器够宽并排,过窄自动堆叠成单栏——避免两栏硬挤致长文本字符级折行
                  gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
                  gap: 16,
                  alignItems: "start",
                }}
              >
                <section role="region" aria-labelledby="requested-governance-contract-title">
                  <Typography.Title level={5} id="requested-governance-contract-title">
                    {t("form.edict.warning.governanceRequestedTitle")}
                  </Typography.Title>
                  <Descriptions
                    size="small"
                    column={1}
                    bordered
                    labelStyle={{ whiteSpace: "nowrap" }}
                  >
                    <Descriptions.Item label={t("form.edict.warning.governanceExecutorLabel")}>
                      {contractValue(requestedExecutor?.adapter_id)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceCapabilitiesLabel")}>
                      <Space direction="vertical" size={4} style={{ width: "100%" }}>
                        {(
                          [
                            ["governanceCapMandatory", requestedCapabilities?.mandatory, undefined],
                            ["governanceCapAdvisory", requestedCapabilities?.advisory, "orange"],
                          ] as const
                        ).map(([labelKey, raw, color]) => {
                          const names = capabilityNames(raw);
                          return (
                            <div key={labelKey}>
                              <Typography.Text
                                type="secondary"
                                style={{ fontSize: 12, marginRight: 4 }}
                              >
                                {t(`form.edict.warning.${labelKey}`)}
                              </Typography.Text>
                              {names.length ? (
                                names.map((c) => (
                                  <Tag key={c} color={color} style={{ marginBottom: 4 }}>
                                    {c}
                                  </Tag>
                                ))
                              ) : (
                                <Typography.Text type="secondary">—</Typography.Text>
                              )}
                            </div>
                          );
                        })}
                      </Space>
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceAllowedPathsLabel")}>
                      {contractValue(requestedPermissions?.allowed_paths)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceReviewPolicyLabel")}>
                      {contractValue(requestedPermissions?.review_policy)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceWorkspaceLabel")}>
                      {contractValue(requestedWorkspace?.source_id)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceRevisionLabel")}>
                      {contractValue(requestedWorkspace?.base_revision)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceNetworkLabel")}>
                      {contractValue(requestedNetwork?.mode)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceHostsLabel")}>
                      {contractValue(requestedNetwork?.allowed_hosts)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceBudgetLabel")}>
                      {t("form.edict.warning.governanceBudgetValue", {
                        tokens: contractValue(requestedBudget?.token_limit),
                        cost: contractValue(requestedBudget?.cost_limit_cny),
                        seconds: contractValue(requestedBudget?.wall_clock_seconds),
                      })}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceAcceptanceLabel")}>
                      {contractValue(requestedAcceptance?.checks)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceDeadlineLabel")}>
                      {contractValue(requestedAcceptance?.deadline_seconds)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceExhaustionLabel")}>
                      {contractValue(requestedAcceptance?.on_exhaustion)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceRecoveryLabel")}>
                      {t("form.edict.warning.governanceRecoveryValue", {
                        restore: contractValue(requestedRecovery?.require_restore_point),
                        cleanup: contractValue(requestedRecovery?.failure_cleanup),
                        rollback: contractValue(requestedRecovery?.rollback_on_apply_failure),
                      })}
                    </Descriptions.Item>
                  </Descriptions>
                </section>
                <section role="region" aria-labelledby="effective-governance-contract-title">
                  <Typography.Title level={5} id="effective-governance-contract-title">
                    {t("form.edict.warning.governanceEffectiveContractTitle")}
                  </Typography.Title>
                  <Descriptions
                    size="small"
                    column={1}
                    bordered
                    labelStyle={{ whiteSpace: "nowrap" }}
                  >
                    <Descriptions.Item label={t("form.edict.warning.governanceExecutorLabel")}>
                      {effectiveContract?.executor_manifest_id ??
                        effectiveContract?.executor.adapter_id ??
                        "-"}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceLevelLabel")}>
                      {pendingGovernanceDispatch.preview.executor_level}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceAllowedPathsLabel")}>
                      {contractValue(effectivePermissions?.allowed_paths)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceReviewPolicyLabel")}>
                      {contractValue(effectivePermissions?.review_policy)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceWorkspaceLabel")}>
                      {contractValue(
                        effectiveContract?.resolved_source_id ?? effectiveWorkspace?.source_id,
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceRevisionLabel")}>
                      {contractValue(
                        effectiveContract?.resolved_base_revision ?? effectiveWorkspace?.base_revision,
                      )}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceNetworkLabel")}>
                      {contractValue(effectiveNetwork?.mode)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceHostsLabel")}>
                      {contractValue(effectiveNetwork?.allowed_hosts)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceBudgetLabel")}>
                      {t("form.edict.warning.governanceBudgetValue", {
                        tokens: contractValue(effectiveBudget?.token_limit),
                        cost: contractValue(effectiveBudget?.cost_limit_cny),
                        seconds: contractValue(effectiveBudget?.wall_clock_seconds),
                      })}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceAcceptanceLabel")}>
                      {contractValue(effectiveAcceptance?.checks)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceDeadlineLabel")}>
                      {contractValue(effectiveAcceptance?.deadline_seconds)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceExhaustionLabel")}>
                      {contractValue(effectiveAcceptance?.on_exhaustion)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceRecoveryLabel")}>
                      {t("form.edict.warning.governanceRecoveryValue", {
                        restore: contractValue(effectiveRecovery?.require_restore_point),
                        cleanup: contractValue(effectiveRecovery?.failure_cleanup),
                        rollback: contractValue(effectiveRecovery?.rollback_on_apply_failure),
                      })}
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceControlsLabel")}>
                      {t("form.edict.warning.governanceEffectiveControls", {
                        enforced: effectiveControlCounts?.enforced ?? 0,
                        bestEffort: effectiveControlCounts?.best_effort ?? 0,
                        observed: effectiveControlCounts?.observed ?? 0,
                        unsupported: effectiveControlCounts?.unsupported ?? 0,
                      })}
                      <ul
                        aria-label={t("form.edict.warning.governanceCapabilitySupportLabel")}
                        style={{ margin: "8px 0 0", paddingInlineStart: 20 }}
                      >
                        {effectiveContract?.effective_controls.map((control) => (
                          <li key={control.capability}>
                            {control.capability} · {control.requested_mode} → {control.state}
                          </li>
                        ))}
                      </ul>
                    </Descriptions.Item>
                    <Descriptions.Item label={t("form.edict.warning.governanceHashLabel")}>
                      {t("form.edict.warning.governanceEffectiveHash", {
                        hash: pendingGovernanceDispatch.preview.requested_contract_hash.slice(0, 12),
                      })}
                    </Descriptions.Item>
                  </Descriptions>
                </section>
              </div>
            </Space>
          }
          action={
            <Space direction="vertical">
              <Button type="primary" onClick={confirmGovernanceDispatch}>
                {t(
                  governanceConfirmation === "always"
                    ? "form.edict.warning.governanceConfirm"
                    : "form.edict.warning.governanceContinue",
                )}
              </Button>
              <Button onClick={reviseGovernanceDispatch}>
                {t("form.edict.warning.governanceRevise")}
              </Button>
            </Space>
          }
        />
      )}

      <Form.Item>
        <Button
          type="primary"
          htmlType="submit"
          loading={loading || governancePreviewLoading}
          icon={<SendOutlined />}
          size="large"
        >
          {t("nav.edictCreate")}
        </Button>
      </Form.Item>
    </Form>
  );
}
