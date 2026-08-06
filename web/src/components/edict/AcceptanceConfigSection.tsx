import {
  Alert,
  Form,
  Input,
  InputNumber,
  Button,
  Select,
  Divider,
  Radio,
  Switch,
  Space,
  Card,
  Collapse,
} from "antd";
import { MinusCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { usePersonas } from "../../hooks/usePersonas";
import { useT } from "../../i18n";

interface AcceptanceConfigSectionProps {
  longTaskEnabled: boolean;
  setLongTaskEnabled: React.Dispatch<React.SetStateAction<boolean>>;
  assignMode: "auto" | "direct";
}

export default function AcceptanceConfigSection({
  longTaskEnabled,
  setLongTaskEnabled,
  assignMode,
}: AcceptanceConfigSectionProps) {
  const t = useT();
  const { data: personas } = usePersonas();

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

  return (
    <>
      <Form.Item
        label={t("form.edict.field.longTaskEnabled")}
        tooltip={t("form.edict.tooltip.longTaskEnabled")}
      >
        <Switch
          checked={longTaskEnabled}
          onChange={setLongTaskEnabled}
          checkedChildren={t("common2.enabled")}
          unCheckedChildren={t("common2.disabled")}
        />
      </Form.Item>

      {longTaskEnabled && (
        <>
          <Form.Item
            name="max_outer_iterations"
            label={t("form.edict.field.maxOuterIterations")}
            tooltip={t("form.edict.tooltip.maxOuterIterations")}
          >
            <InputNumber
              min={1}
              max={50}
              style={{ width: "100%" }}
              placeholder={t("form.edict.placeholder.maxOuterIterations")}
            />
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
            rules={[
              {
                required: true,
                type: "array",
                min: 1,
                message: t("form.edict.validation.criticPersonasRequired"),
              },
            ]}
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
              const exec = getFieldValue("assigned_persona_id") as
                string | undefined;
              const critics =
                (getFieldValue("critic_persona_ids") as string[] | undefined) ??
                [];
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

          <Collapse
            ghost
            style={{ marginBottom: 8 }}
            items={[
              {
                key: "adv-acceptance",
                label: t("form.edict.section.advancedAcceptance"),
                children: (
                  <>
                    <Form.Item
                      name="min_outer_iterations"
                      label={t("form.edict.field.minOuterIterations")}
                      tooltip={t("form.edict.tooltip.minOuterIterations")}
                    >
                      <InputNumber
                        min={1}
                        max={20}
                        style={{ width: "100%" }}
                        placeholder={t(
                          "form.edict.placeholder.minOuterIterations",
                        )}
                      />
                    </Form.Item>

                    <Form.Item
                      name="same_issue_threshold"
                      label={t("form.edict.field.sameIssueThreshold")}
                      tooltip={t("form.edict.tooltip.sameIssueThreshold")}
                    >
                      <InputNumber
                        min={1}
                        max={10}
                        style={{ width: "100%" }}
                        placeholder={t(
                          "form.edict.placeholder.sameIssueThreshold",
                        )}
                      />
                    </Form.Item>

                    <Form.Item
                      name="l1_max_rounds"
                      label={t("form.edict.field.l1MaxRounds")}
                    >
                      <InputNumber
                        min={1}
                        max={5}
                        style={{ width: "100%" }}
                        placeholder={t("form.edict.placeholder.l1MaxRounds")}
                      />
                    </Form.Item>

                    <Form.Item
                      name="l2_max_rounds"
                      label={t("form.edict.field.l2MaxRounds")}
                    >
                      <InputNumber
                        min={1}
                        max={5}
                        style={{ width: "100%" }}
                        placeholder={t("form.edict.placeholder.l2MaxRounds")}
                      />
                    </Form.Item>
                  </>
                ),
              },
            ]}
          />

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
                      rules={[
                        {
                          required: true,
                          message: t("form.check.validation.nameRequired"),
                        },
                      ]}
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
                                rules={[
                                  {
                                    required: true,
                                    message: t(
                                      "form.check.validation.rubricRequired",
                                    ),
                                  },
                                ]}
                              >
                                <Input.TextArea
                                  rows={3}
                                  placeholder={t(
                                    "form.check.placeholder.rubric",
                                  )}
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
                            rules={[
                              {
                                required: true,
                                message: t(
                                  "form.check.validation.commandRequired",
                                ),
                              },
                            ]}
                          >
                            <Input
                              placeholder={t("form.check.placeholder.command")}
                            />
                          </Form.Item>
                        );
                      }}
                    </Form.Item>
                    <Form.Item
                      name={[name, "timeout_seconds"]}
                      label={t("form.check.field.timeout")}
                      initialValue={60}
                    >
                      <InputNumber
                        min={1}
                        max={3600}
                        style={{ width: "100%" }}
                      />
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
  );
}
