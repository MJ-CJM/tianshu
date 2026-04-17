import { useState } from "react";
import { Form, Input, InputNumber, Button, Collapse, Select, Divider, Radio, Switch } from "antd";
import { SendOutlined } from "@ant-design/icons";
import { usePersonas } from "../../hooks/usePersonas";
import type { EdictCreateRequest, EdictRuntime } from "../../api/types";
import PolicyProfilePanel from "../policy/PolicyProfilePanel";
import type { PolicyProfileValue } from "../policy/PolicyProfilePanel";

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
