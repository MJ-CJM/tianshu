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
import {
  Alert,
  Collapse,
  Form,
  InputNumber,
  Radio,
  Select,
  Switch,
  Typography,
} from "antd";
import { usePersonas } from "../../hooks/usePersonas";
import type { AcceptanceCriteria, EdictRuntime } from "../../api/types";
import { useT } from "../../i18n";

export interface FollowUpOverrideValue {
  runtime_override?: Partial<EdictRuntime>;
  acceptance_override?: AcceptanceCriteria;
}

interface Props {
  onChange: (v: FollowUpOverrideValue) => void;
  assignedPersonaId?: string | null;
}

export default function FollowUpOverridePanel({
  onChange,
  assignedPersonaId,
}: Props) {
  const t = useT();
  const [form] = Form.useForm();
  const [longTaskEnabled, setLongTaskEnabled] = useState(false);
  const { data: personas } = usePersonas();

  const criticOptions = useMemo(
    () =>
      (personas ?? []).map((p) => ({
        value: p.id,
        label: `${p.name} · ${t(`dept.${p.department}`)}`,
      })),
    [personas, t],
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
      <Typography.Text
        type="secondary"
        style={{ fontSize: 12, display: "block", marginBottom: 8 }}
      >
        {t("comp.followUp.hint")}
      </Typography.Text>

      <Collapse
        size="small"
        ghost
        items={[
          {
            key: "advanced",
            label: t("comp.followUp.advanced"),
            children: (
              <>
                <Form.Item
                  name="timeout_seconds"
                  label={t("comp.followUp.timeoutLabel")}
                >
                  <InputNumber
                    min={10}
                    max={3600}
                    style={{ width: "100%" }}
                    placeholder={t("comp.followUp.placeholderInherit")}
                  />
                </Form.Item>
                <Form.Item
                  name="max_iterations"
                  label={t("comp.followUp.maxIterLabel")}
                >
                  <InputNumber
                    min={1}
                    max={200}
                    style={{ width: "100%" }}
                    placeholder={t("comp.followUp.placeholderInherit")}
                  />
                </Form.Item>
                <Form.Item
                  name="token_budget"
                  label={t("comp.followUp.tokenBudgetLabel")}
                >
                  <InputNumber
                    min={1}
                    style={{ width: "100%" }}
                    placeholder={t("comp.followUp.placeholderInherit")}
                  />
                </Form.Item>
                <Form.Item
                  name="cost_budget_cny"
                  label={t("comp.followUp.costBudgetLabel")}
                >
                  <InputNumber
                    min={0}
                    step={0.01}
                    style={{ width: "100%" }}
                    placeholder={t("comp.followUp.placeholderInherit")}
                  />
                </Form.Item>
              </>
            ),
          },
          {
            key: "long-task",
            label: t("comp.followUp.longTask"),
            children: (
              <>
                <Form.Item
                  label={t("comp.followUp.longTaskEnableLabel")}
                  tooltip={t("comp.followUp.longTaskEnableTooltip")}
                >
                  <Switch
                    checked={longTaskEnabled}
                    onChange={handleLongTaskToggle}
                    checkedChildren={t("common2.enabled")}
                    unCheckedChildren={t("common2.disabled")}
                  />
                </Form.Item>

                {longTaskEnabled && (
                  <>
                    <Form.Item
                      name="critic_persona_ids"
                      label={t("comp.followUp.criticLabel")}
                      rules={[
                        {
                          required: true,
                          type: "array",
                          min: 1,
                          message: t("comp.followUp.criticRequired"),
                        },
                      ]}
                    >
                      <Select
                        mode="multiple"
                        options={criticOptions}
                        showSearch
                        optionFilterProp="label"
                        placeholder={t("comp.followUp.criticPlaceholder")}
                      />
                    </Form.Item>

                    <Form.Item
                      noStyle
                      shouldUpdate={(p, c) =>
                        p.critic_persona_ids !== c.critic_persona_ids
                      }
                    >
                      {({ getFieldValue }) => {
                        if (!assignedPersonaId) return null;
                        const critics =
                          (getFieldValue("critic_persona_ids") as
                            string[] | undefined) ?? [];
                        if (!critics.includes(assignedPersonaId)) return null;
                        return (
                          <Alert
                            type="warning"
                            showIcon
                            style={{ marginBottom: 16 }}
                            message={t("comp.followUp.criticOverlapTitle")}
                            description={t("comp.followUp.criticOverlapDesc", {
                              id: assignedPersonaId,
                            })}
                          />
                        );
                      }}
                    </Form.Item>
                    <Form.Item
                      name="critic_strictness"
                      label={t("comp.followUp.strictnessLabel")}
                      initialValue="lenient"
                    >
                      <Radio.Group>
                        <Radio value="lenient">{t("strictness.lenient")}</Radio>
                        <Radio value="balanced">
                          {t("strictness.balanced")}
                        </Radio>
                        <Radio value="strict">{t("strictness.strict")}</Radio>
                      </Radio.Group>
                    </Form.Item>
                    <Form.Item
                      name="max_outer_iterations"
                      label={t("comp.followUp.maxOuterLabel")}
                      initialValue={5}
                    >
                      <InputNumber min={1} max={50} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item
                      name="min_outer_iterations"
                      label={t("comp.followUp.minOuterLabel")}
                      tooltip={t("comp.followUp.minOuterTooltip")}
                    >
                      <InputNumber
                        min={1}
                        max={20}
                        style={{ width: "100%" }}
                        placeholder={t("comp.followUp.placeholderInherit")}
                      />
                    </Form.Item>
                    <Form.Item
                      name="on_exhaustion"
                      label={t("comp.followUp.exhaustionLabel")}
                      initialValue="escalate"
                    >
                      <Radio.Group>
                        <Radio value="escalate">
                          {t("exhaustion.shortEscalate")}
                        </Radio>
                        <Radio value="best_effort">
                          {t("exhaustion.shortBestEffort")}
                        </Radio>
                        <Radio value="fail">{t("exhaustion.shortFail")}</Radio>
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

  // acceptance override（仅在 longTaskEnabled 时构建）
  if (longTaskEnabled) {
    const acceptance: AcceptanceCriteria = {};
    const personaIds = values.critic_persona_ids as string[] | undefined;
    const strictness = values.critic_strictness as
      "lenient" | "balanced" | "strict" | undefined;
    if ((personaIds && personaIds.length > 0) || strictness) {
      acceptance.critic = {
        ...(personaIds && personaIds.length > 0
          ? { persona_ids: personaIds }
          : {}),
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
      "escalate" | "best_effort" | "fail" | undefined;
    if (onExhaustion) acceptance.on_exhaustion = onExhaustion;
    result.acceptance_override = acceptance;
  }

  return result;
}
