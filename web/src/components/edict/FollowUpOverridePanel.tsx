/** 继续批示时本次单独覆盖 edict 配置的折叠面板。
 *
 * 设计理念：
 * - 默认收起 + 字段全空 = 沿用 edict 原配置（情况 1）
 * - 「长任务模式」面板 Switch 启用后，本次 follow-up 升级为 outer loop（情况 2）
 * - 「高级选项」面板填具体字段则覆盖 edict.runtime（情况 3）
 *
 * 不重构 EdictForm；这里只暴露 follow-up 最常需要的子集字段。
 * Checks / Policy / Network / Deadline 等进阶字段不开放（避免 UI 过载）。
 */

import { useMemo, useState } from "react";
import { Alert, Collapse, Form, InputNumber, Radio, Select, Switch, Typography } from "antd";
import { usePersonas } from "../../hooks/usePersonas";
import type { AcceptanceCriteria, EdictRuntime } from "../../api/types";

export interface FollowUpOverrideValue {
  runtime_override?: Partial<EdictRuntime>;
  acceptance_override?: AcceptanceCriteria;
}

interface Props {
  /** 受控值；父组件读这两个字段塞进 followUpEdict body */
  onChange: (v: FollowUpOverrideValue) => void;
  /** 当前敕令的执行官 ID — 用于判断是否与所选监督官重合 */
  assignedPersonaId?: string | null;
}

const DEPT_LABEL: Record<string, string> = {
  ducha: "都察院",
  neige: "内阁",
  bingbu: "兵部",
  hubu: "户部",
  wenyuan: "文渊阁",
  tongzheng: "通政司",
};

export default function FollowUpOverridePanel({ onChange, assignedPersonaId }: Props) {
  const [form] = Form.useForm();
  const [longTaskEnabled, setLongTaskEnabled] = useState(false);
  const { data: personas } = usePersonas();

  const criticOptions = useMemo(
    () =>
      (personas ?? []).map((p) => ({
        value: p.id,
        label: `${p.name} · ${DEPT_LABEL[p.department] ?? p.department}`,
      })),
    [personas],
  );

  const handleValuesChange = (
    _changed: Record<string, unknown>,
    all: Record<string, unknown>,
  ) => {
    onChange(buildOverride(all, longTaskEnabled));
  };

  const handleLongTaskToggle = (checked: boolean) => {
    setLongTaskEnabled(checked);
    onChange(buildOverride(form.getFieldsValue(), checked));
  };

  return (
    <Form
      form={form}
      layout="vertical"
      onValuesChange={handleValuesChange}
      style={{ marginTop: 12 }}
    >
      <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 8 }}>
        留空 = 沿用本敕令配置；填写即本次覆盖（不影响后续继续批示）。
      </Typography.Text>

      <Collapse
        size="small"
        ghost
        items={[
          {
            key: "advanced",
            label: "高级选项（本次覆盖）",
            children: (
              <>
                <Form.Item name="timeout_seconds" label="超时时间 (秒)">
                  <InputNumber min={10} max={3600} style={{ width: "100%" }} placeholder="留空沿用" />
                </Form.Item>
                <Form.Item name="max_iterations" label="最大迭代次数">
                  <InputNumber min={1} max={200} style={{ width: "100%" }} placeholder="留空沿用" />
                </Form.Item>
                <Form.Item name="token_budget" label="Token 预算">
                  <InputNumber min={1} style={{ width: "100%" }} placeholder="留空沿用" />
                </Form.Item>
                <Form.Item name="cost_budget_cny" label="费用预算 (CNY)">
                  <InputNumber min={0} step={0.01} style={{ width: "100%" }} placeholder="留空沿用" />
                </Form.Item>
                <Form.Item name="review_policy" label="审核策略">
                  <Select
                    allowClear
                    placeholder="留空沿用"
                    options={[
                      { value: "always", label: "始终人工复核" },
                      { value: "on_flag", label: "审计标记时人工复核" },
                      { value: "on_failure", label: "失败时人工复核" },
                      { value: "never", label: "跳过人工复核" },
                    ]}
                  />
                </Form.Item>
              </>
            ),
          },
          {
            key: "long-task",
            label: "长任务模式 (本次覆盖)",
            children: (
              <>
                <Form.Item label="本次启用长任务模式" tooltip="启用后本次 follow-up 走 actor → checks → critic 多轮路径">
                  <Switch
                    checked={longTaskEnabled}
                    onChange={handleLongTaskToggle}
                    checkedChildren="启用"
                    unCheckedChildren="关闭"
                  />
                </Form.Item>

                {longTaskEnabled && (
                  <>
                    <Form.Item
                      name="critic_persona_ids"
                      label="监督官 (可多选)"
                      rules={[{ required: true, type: "array", min: 1, message: "至少选一位监督官" }]}
                    >
                      <Select
                        mode="multiple"
                        options={criticOptions}
                        showSearch
                        optionFilterProp="label"
                        placeholder="选择监督官"
                      />
                    </Form.Item>

                    <Form.Item
                      noStyle
                      shouldUpdate={(p, c) => p.critic_persona_ids !== c.critic_persona_ids}
                    >
                      {({ getFieldValue }) => {
                        if (!assignedPersonaId) return null;
                        const critics = (getFieldValue("critic_persona_ids") as string[] | undefined) ?? [];
                        if (!critics.includes(assignedPersonaId)) return null;
                        return (
                          <Alert
                            type="warning"
                            showIcon
                            style={{ marginBottom: 16 }}
                            message="执行官与监督官重合"
                            description={`本敕令的执行官（${assignedPersonaId}）也被选为监督官，自我审议会显著降低 critic 客观性，建议改选都察院 / 文渊阁等独立 persona。`}
                          />
                        );
                      }}
                    </Form.Item>
                    <Form.Item name="critic_strictness" label="Critic 严苛度" initialValue="lenient">
                      <Radio.Group>
                        <Radio value="lenient">宽松</Radio>
                        <Radio value="balanced">高标准</Radio>
                        <Radio value="strict">严苛</Radio>
                      </Radio.Group>
                    </Form.Item>
                    <Form.Item
                      name="max_outer_iterations"
                      label="最多迭代轮数"
                      initialValue={5}
                    >
                      <InputNumber min={1} max={50} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item
                      name="min_outer_iterations"
                      label="最少迭代轮数 (持续优化)"
                      tooltip="≥2 时即使 critic 第一轮 PASS 也强制继续"
                    >
                      <InputNumber min={1} max={20} style={{ width: "100%" }} placeholder="默认 1" />
                    </Form.Item>
                    <Form.Item name="on_exhaustion" label="耗尽时" initialValue="escalate">
                      <Radio.Group>
                        <Radio value="escalate">上报人工</Radio>
                        <Radio value="best_effort">取最近一轮</Radio>
                        <Radio value="fail">失败</Radio>
                      </Radio.Group>
                    </Form.Item>
                  </>
                )}
              </>
            ),
          },
        ]}
      />
    </Form>
  );
}

/** 把表单原始 values 压成两个 override 对象（只保留用户实际填写的字段） */
function buildOverride(
  values: Record<string, unknown>,
  longTaskEnabled: boolean,
): FollowUpOverrideValue {
  const result: FollowUpOverrideValue = {};

  // runtime override
  const runtime: Partial<EdictRuntime> = {};
  const t = values.timeout_seconds as number | undefined;
  if (t !== undefined && t !== null) runtime.timeout_seconds = t;
  const mi = values.max_iterations as number | undefined;
  if (mi !== undefined && mi !== null) runtime.max_iterations = mi;
  const tb = values.token_budget as number | undefined;
  if (tb !== undefined && tb !== null) runtime.token_budget = tb;
  const cb = values.cost_budget_cny as number | undefined;
  if (cb !== undefined && cb !== null) runtime.cost_budget_cny = cb;
  if (Object.keys(runtime).length > 0) result.runtime_override = runtime;

  const reviewPolicy = values.review_policy as string | undefined;
  // review_policy 在 edict 顶层而非 runtime — 放进 runtime_override 后端会忽略；
  // 因此 follow-up 暂不支持改 review_policy（明确不放，避免静默失败）。
  // 若未来支持，需要扩展 Memorial.acceptance_override 之外的 edict-level override。
  void reviewPolicy;

  // acceptance override（仅在 longTaskEnabled 时构建）
  if (longTaskEnabled) {
    const acceptance: AcceptanceCriteria = {};
    const personaIds = values.critic_persona_ids as string[] | undefined;
    const strictness = values.critic_strictness as
      | "lenient"
      | "balanced"
      | "strict"
      | undefined;
    if ((personaIds && personaIds.length > 0) || strictness) {
      acceptance.critic = {
        ...(personaIds && personaIds.length > 0 ? { persona_ids: personaIds } : {}),
        ...(strictness ? { strictness } : {}),
      };
    }
    const maxOuter = values.max_outer_iterations as number | undefined;
    if (maxOuter !== undefined && maxOuter !== null) {
      acceptance.max_outer_iterations = maxOuter;
    }
    const minOuter = values.min_outer_iterations as number | undefined;
    if (minOuter !== undefined && minOuter !== null && minOuter > 1) {
      acceptance.min_outer_iterations = minOuter;
    }
    const onExhaustion = values.on_exhaustion as
      | "escalate"
      | "best_effort"
      | "fail"
      | undefined;
    if (onExhaustion) acceptance.on_exhaustion = onExhaustion;
    result.acceptance_override = acceptance;
  }

  return result;
}
