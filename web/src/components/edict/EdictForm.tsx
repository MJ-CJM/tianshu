import { useState } from "react";
import { Form, Input, InputNumber, Button, Collapse, Select, Divider, Radio, Switch, Space, Card } from "antd";
import { SendOutlined, MinusCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { usePersonas } from "../../hooks/usePersonas";
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
  const [form] = Form.useForm();
  const [scheduleType, setScheduleType] = useState("immediate");
  const [assignMode, setAssignMode] = useState<"auto" | "direct">("auto");
  const [policyProfile, setPolicyProfile] =
    useState<PolicyProfileValue | null>(null);
  const [longTaskEnabled, setLongTaskEnabled] = useState(false);
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

  // critic 监督官候选：所有 personas（推荐都察院/内阁系列）
  const DEPT_LABEL: Record<string, string> = {
    ducha: "都察院",
    neige: "内阁",
    bingbu: "兵部",
    hubu: "户部",
    wenyuan: "文渊阁",
    tongzheng: "通政司",
  };
  const criticPersonas = personas ?? [];
  const criticPersonaOptions = criticPersonas.map((p) => ({
    value: p.id,
    label: `${p.name} · ${DEPT_LABEL[p.department] ?? p.department}${p.llm_config_name ? ` (${p.llm_config_name})` : ""}`,
  }));
  // 默认值：优先 ducha → neige → 第一个
  const defaultCriticPersonaIds = (() => {
    const ducha = criticPersonas.find((p) => p.department === "ducha")?.id;
    if (ducha) return [ducha];
    const neige = criticPersonas.find((p) => p.department === "neige")?.id;
    if (neige) return [neige];
    return criticPersonas[0] ? [criticPersonas[0].id] : [];
  })();

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

    // 长任务 outer loop 字段
    if (longTaskEnabled) {
      const acceptance: AcceptanceCriteria = {};
      const maxOuter = values.max_outer_iterations as number | undefined;
      if (maxOuter !== undefined && maxOuter !== 5) {
        acceptance.max_outer_iterations = maxOuter;
      }
      const deadline = values.deadline_seconds as number | undefined;
      if (deadline) {
        acceptance.deadline_seconds = deadline;
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
      if (sameIssueThreshold !== undefined || (criticPersonaIds && criticPersonaIds.length > 0)) {
        acceptance.critic = {
          ...(criticPersonaIds && criticPersonaIds.length > 0
            ? { persona_ids: criticPersonaIds }
            : {}),
          ...(sameIssueThreshold !== undefined && sameIssueThreshold !== 2
            ? { same_issue_threshold: sameIssueThreshold }
            : {}),
        };
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
      <Form.Item name="title" label="敕令标题（可选）">
        <Input placeholder="留空则自动截取旨意前 20 字" />
      </Form.Item>

      <Form.Item
        name="goal"
        label="敕令旨意"
        rules={[{ required: true, message: "请拟定敕令旨意" }]}
      >
        <Input.TextArea
          rows={4}
          placeholder="请拟定敕令旨意..."
          style={{ resize: "vertical" }}
        />
      </Form.Item>

      <Form.Item label="执行方式">
        <Radio.Group
          value={assignMode}
          onChange={(e) => {
            setAssignMode(e.target.value);
            if (e.target.value === "auto") {
              form.setFieldValue("assigned_persona_id", undefined);
            }
          }}
        >
          <Radio value="auto">内阁决策（自动规划分配）</Radio>
          <Radio value="direct">直接指派官员</Radio>
        </Radio.Group>
      </Form.Item>

      {assignMode === "direct" && (
        <Form.Item
          name="assigned_persona_id"
          rules={[{ required: true, message: "请选择执行官员" }]}
        >
          <Select
            placeholder="选择官员"
            options={personaOptions}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
      )}

      {assignMode === "auto" && cabinetPersonas.length > 1 && (
        <Form.Item
          name="planner_persona_id"
          label="规划官（可选）"
          tooltip="选择使用哪位内阁官员进行规划，不同官员可使用不同的 LLM"
        >
          <Select
            placeholder="自动（使用全局配置）"
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
          label="规划审批"
          valuePropName="checked"
          tooltip="开启后，规划方案需人工审批通过后才会执行"
        >
          <Switch />
        </Form.Item>
      )}

      <Form.Item name="context" label="附则（可选）">
        <Input.TextArea
          rows={3}
          placeholder="补充背景信息或约束条件..."
          style={{ resize: "vertical" }}
        />
      </Form.Item>

      <Collapse
        ghost
        style={{ marginBottom: 24 }}
        items={[
          {
            key: "advanced",
            label: "高级选项",
            children: (
              <>
                <Form.Item
                  name="schedule_type"
                  label="调度方式"
                >
                  <Select
                    onChange={(v) => setScheduleType(v)}
                    options={[
                      { value: "immediate", label: "即时执行" },
                      { value: "once", label: "定时执行" },
                      { value: "cron", label: "周期执行" },
                    ]}
                  />
                </Form.Item>

                {scheduleType === "cron" && (
                  <Form.Item
                    name="cron_expr"
                    label="Cron 表达式"
                    rules={[{ required: true, message: "请输入 Cron 表达式" }]}
                  >
                    <Input placeholder="例如 0 9 * * 1-5" />
                  </Form.Item>
                )}

                {scheduleType === "once" && (
                  <Form.Item
                    name="schedule_at"
                    label="执行时间"
                    rules={[{ required: true, message: "请输入执行时间" }]}
                  >
                    <Input placeholder="ISO 8601 时间，例如 2026-03-20T09:00:00" />
                  </Form.Item>
                )}

                <Form.Item
                  name="priority"
                  label="优先级"
                >
                  <Select
                    options={[
                      { value: "urgent", label: "紧急" },
                      { value: "normal", label: "普通" },
                      { value: "low", label: "低" },
                    ]}
                  />
                </Form.Item>

                <Form.Item
                  name="review_policy"
                  label="审核策略"
                >
                  <Select
                    options={[
                      { value: "always", label: "始终人工复核" },
                      { value: "on_flag", label: "自动（审计标记时人工复核）" },
                      { value: "on_failure", label: "失败时人工复核" },
                      { value: "never", label: "跳过人工复核" },
                    ]}
                  />
                </Form.Item>

                <Form.Item
                  name="constraints"
                  label="约束条件"
                >
                  <Select
                    mode="tags"
                    placeholder="输入约束条件后按回车添加"
                    tokenSeparators={[","]}
                  />
                </Form.Item>

                <Form.Item
                  name="output_format"
                  label="输出格式"
                >
                  <Input.TextArea
                    rows={2}
                    placeholder="指定期望的输出格式，如 JSON、Markdown 表格等"
                    style={{ resize: "vertical" }}
                  />
                </Form.Item>

                <Divider style={{ margin: "12px 0" }} />
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>
                  执行参数
                </div>

                <Form.Item name="timeout_seconds" label="超时时间 (秒)">
                  <InputNumber min={10} max={3600} style={{ width: "100%" }} placeholder="默认 300" />
                </Form.Item>

                <Form.Item name="max_iterations" label="最大迭代次数">
                  <InputNumber min={1} max={200} style={{ width: "100%" }} placeholder="默认 20" />
                </Form.Item>

                <Form.Item name="max_concurrency" label="DAG 并发度">
                  <InputNumber min={1} max={8} style={{ width: "100%" }} placeholder="默认 1" />
                </Form.Item>

                <Form.Item name="retry_limit" label="重试次数">
                  <InputNumber min={0} max={10} style={{ width: "100%" }} placeholder="默认 0" />
                </Form.Item>

                <Form.Item name="token_budget" label="Token 预算">
                  <InputNumber min={1} style={{ width: "100%" }} placeholder="不限" />
                </Form.Item>

                <Form.Item name="cost_budget_cny" label="费用预算 (CNY)">
                  <InputNumber min={0} step={0.01} style={{ width: "100%" }} placeholder="不限" />
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
            label: "长任务模式 (Outer Loop)",
            children: (
              <>
                <Form.Item label="启用长任务模式" tooltip="启用后 edict 走 actor → checks → critic 的多轮迭代路径，可自驱收敛；不启用则走单回合 agent loop（默认）">
                  <Switch
                    checked={longTaskEnabled}
                    onChange={setLongTaskEnabled}
                    checkedChildren="启用"
                    unCheckedChildren="关闭"
                  />
                </Form.Item>

                {longTaskEnabled && (
                  <>
                    <Form.Item label="模板预设" tooltip="一键填入常用配置；填完仍可在下方继续微调">
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
                          📊 数据分析型
                        </Button>
                        <Button
                          size="small"
                          onClick={() => {
                            form.setFieldsValue({
                              execution_profile: "foreground",
                              max_outer_iterations: 10,
                              on_exhaustion: "best_effort",
                              on_critic_unavailable: "skip",
                              same_issue_threshold: 3,
                              critic_persona_ids: defaultCriticPersonaIds,
                            });
                          }}
                        >
                          ✍️ 内容创作型
                        </Button>
                        <Button
                          size="small"
                          onClick={() => {
                            form.setFieldsValue({
                              execution_profile: "checkpointed",
                              max_outer_iterations: 8,
                              deadline_seconds: 3600,
                              on_exhaustion: "escalate",
                              on_critic_unavailable: "escalate",
                              same_issue_threshold: 2,
                              critic_persona_ids: defaultCriticPersonaIds,
                            });
                          }}
                        >
                          🛠 代码实现型
                        </Button>
                        <Button
                          size="small"
                          onClick={() => {
                            form.setFieldsValue({
                              execution_profile: "background",
                              max_outer_iterations: 15,
                              deadline_seconds: 7200,
                              on_exhaustion: "best_effort",
                              on_critic_unavailable: "skip",
                              same_issue_threshold: 3,
                              critic_persona_ids: defaultCriticPersonaIds,
                            });
                          }}
                        >
                          🔬 深度研究型
                        </Button>
                      </Space>
                    </Form.Item>

                    <Form.Item
                      name="execution_profile"
                      label="执行模型"
                      tooltip="foreground=同进程；checkpointed=每轮 checkpoint 可续跑；background=后台跑（用户可关页面）"
                      initialValue="foreground"
                    >
                      <Radio.Group>
                        <Radio value="foreground">前台</Radio>
                        <Radio value="checkpointed">可续跑</Radio>
                        <Radio value="background">后台</Radio>
                      </Radio.Group>
                    </Form.Item>

                    <Form.Item
                      name="max_outer_iterations"
                      label="最多迭代轮数"
                      tooltip="actor → critic 一回合算一轮；超过此数走 on_exhaustion 决策"
                    >
                      <InputNumber min={1} max={50} style={{ width: "100%" }} placeholder="默认 5" />
                    </Form.Item>

                    <Form.Item
                      name="deadline_seconds"
                      label="截止时间 (秒，可选)"
                      tooltip="超过则触发 EXHAUSTED；空=无硬截止"
                    >
                      <InputNumber min={60} style={{ width: "100%" }} placeholder="不限" />
                    </Form.Item>

                    <Form.Item
                      name="on_exhaustion"
                      label="耗尽时"
                      tooltip="超过 max_outer_iterations / deadline / cost_budget 时的处理"
                      initialValue="escalate"
                    >
                      <Radio.Group>
                        <Radio value="escalate">上报人工 (L3)</Radio>
                        <Radio value="best_effort">取最近一轮当成果</Radio>
                        <Radio value="fail">直接失败</Radio>
                      </Radio.Group>
                    </Form.Item>

                    <Form.Item
                      name="on_critic_unavailable"
                      label="Critic 故障时"
                      tooltip="critic LLM 调用全部失败的兜底"
                      initialValue="skip"
                    >
                      <Radio.Group>
                        <Radio value="skip">跳过当作通过</Radio>
                        <Radio value="escalate">上报人工</Radio>
                      </Radio.Group>
                    </Form.Item>

                    <Form.Item
                      name="critic_persona_ids"
                      label="监督官 (Critic Personas，可多选)"
                      tooltip="谁来监督任务质量。多选时每轮并发评审，按'全过则过'聚合；任意一位 FAIL 即不过。终态后每位监督官独立产出监督报告。"
                      rules={[{
                        required: true,
                        type: "array",
                        min: 1,
                        message: "启用长任务时至少选择一位监督官",
                      }]}
                      initialValue={defaultCriticPersonaIds}
                    >
                      <Select
                        mode="multiple"
                        options={criticPersonaOptions}
                        showSearch
                        optionFilterProp="label"
                        placeholder="可选多位 persona（都察院/内阁推荐）"
                      />
                    </Form.Item>

                    <Form.Item
                      name="same_issue_threshold"
                      label="同类问题升级阈值"
                      tooltip="critic 连续 N 轮报同类问题（issue_class）则升 L1"
                    >
                      <InputNumber min={1} max={10} style={{ width: "100%" }} placeholder="默认 2" />
                    </Form.Item>

                    <Form.Item name="l1_max_rounds" label="L1 最多重试轮数">
                      <InputNumber min={1} max={5} style={{ width: "100%" }} placeholder="默认 2" />
                    </Form.Item>

                    <Form.Item name="l2_max_rounds" label="L2 最多重试轮数">
                      <InputNumber min={1} max={5} style={{ width: "100%" }} placeholder="默认 1" />
                    </Form.Item>

                    <Divider style={{ margin: "12px 0" }} />
                    <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>
                      可验收指标 Checks（可选，零或多条）
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
                                label="名称"
                                rules={[{ required: true, message: "必填" }]}
                              >
                                <Input placeholder="例如 pytest / tone-check" />
                              </Form.Item>
                              <Form.Item
                                name={[name, "kind"]}
                                label="类型"
                                initialValue="bash"
                              >
                                <Radio.Group>
                                  <Radio value="bash">Bash</Radio>
                                  <Radio value="lint">Lint</Radio>
                                  <Radio value="rubric">Rubric (LLM 评分)</Radio>
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
                                          label="Rubric 提示词"
                                          rules={[{ required: true, message: "rubric 必填" }]}
                                        >
                                          <Input.TextArea
                                            rows={3}
                                            placeholder="评估标准描述，要求 LLM 回 0~1 分"
                                          />
                                        </Form.Item>
                                        <Form.Item
                                          name={[name, "pass_threshold"]}
                                          label="通过阈值"
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
                                      label="Shell 命令"
                                      rules={[{ required: true, message: "command 必填" }]}
                                    >
                                      <Input placeholder="例如 pytest tests/ -q" />
                                    </Form.Item>
                                  );
                                }}
                              </Form.Item>
                              <Form.Item
                                name={[name, "timeout_seconds"]}
                                label="超时 (秒)"
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
                              添加 Bash/Lint Check
                            </Button>
                            <Button
                              type="dashed"
                              icon={<PlusOutlined />}
                              onClick={() =>
                                add({ kind: "rubric", name: "", pass_threshold: 0.8 })
                              }
                            >
                              添加 Rubric Check
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
          颁发敕令
        </Button>
      </Form.Item>
    </Form>
  );
}
