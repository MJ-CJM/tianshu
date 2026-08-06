import { useCallback, useEffect, useState } from "react";
import {
  Collapse,
  Form,
  Select,
  Input,
  InputNumber,
  Space,
  Typography,
  Tag,
} from "antd";
import { SafetyOutlined } from "@ant-design/icons";
import { fetchPolicyTemplates } from "../../api/policy";
import type { PolicyTemplate } from "../../api/policy";
import { toApiProblem } from "../../api/client";
import type { ApiProblem } from "../../contracts/api";
import PageDataState from "../states/PageDataState";
import { problemPageStatus } from "../states/problemPageStatus";
import { useT } from "../../i18n";

const { Text } = Typography;

export interface PolicyProfileValue {
  template_name: string | null;
  allowed_paths: string[];
  allowed_bash_prefixes: string[];
  tier_overrides: Record<string, number>;
  auto_approve_max_tier: number;
  expires_after_seconds: number | null;
}

interface PolicyProfilePanelProps {
  value?: PolicyProfileValue;
  onChange?: (value: PolicyProfileValue | null) => void;
}

const DEFAULT_VALUE: PolicyProfileValue = {
  template_name: null,
  allowed_paths: [],
  allowed_bash_prefixes: [],
  tier_overrides: {},
  auto_approve_max_tier: 1,
  expires_after_seconds: null,
};

export default function PolicyProfilePanel({
  value,
  onChange,
}: PolicyProfilePanelProps) {
  const t = useT();
  const [templates, setTemplates] = useState<PolicyTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesProblem, setTemplatesProblem] = useState<ApiProblem | null>(
    null,
  );
  const [local, setLocal] = useState<PolicyProfileValue>(
    value ?? DEFAULT_VALUE,
  );

  const loadTemplates = useCallback(() => {
    setTemplatesLoading(true);
    setTemplatesProblem(null);
    void fetchPolicyTemplates()
      .then(setTemplates)
      .catch((error: unknown) => setTemplatesProblem(toApiProblem(error)))
      .finally(() => setTemplatesLoading(false));
  }, []);

  useEffect(loadTemplates, [loadTemplates]);

  useEffect(() => {
    if (value) {
      setLocal(value);
    }
  }, [value]);

  const update = (patch: Partial<PolicyProfileValue>) => {
    const next = { ...local, ...patch };
    setLocal(next);
    const isEmpty =
      !next.template_name &&
      next.allowed_paths.length === 0 &&
      next.allowed_bash_prefixes.length === 0;
    onChange?.(isEmpty ? null : next);
  };

  const applyTemplate = (templateName: string | undefined) => {
    if (!templateName) {
      update({
        template_name: null,
        allowed_paths: [],
        allowed_bash_prefixes: [],
        tier_overrides: {},
        auto_approve_max_tier: 1,
      });
      return;
    }
    const tpl = templates.find((t) => t.name === templateName);
    if (!tpl) return;
    update({
      template_name: tpl.name,
      allowed_paths: [...tpl.allowed_paths],
      allowed_bash_prefixes: [...tpl.allowed_bash_prefixes],
      tier_overrides: { ...tpl.tier_overrides },
      auto_approve_max_tier: tpl.auto_approve_max_tier,
    });
  };

  if (templatesLoading) {
    return (
      <PageDataState
        status="loading"
        data={null}
        isEmpty={(items: PolicyTemplate[]) => items.length === 0}
      >
        {() => null}
      </PageDataState>
    );
  }

  if (templatesProblem) {
    return (
      <PageDataState
        status={problemPageStatus(templatesProblem)}
        data={null}
        problem={templatesProblem}
        isEmpty={(items: PolicyTemplate[]) => items.length === 0}
        onRetry={loadTemplates}
      >
        {() => null}
      </PageDataState>
    );
  }

  return (
    <Collapse
      ghost
      items={[
        {
          key: "policy",
          label: (
            <Space>
              <SafetyOutlined />
              <span>{t("comp.policyProfile.title")}</span>
              {local.template_name && (
                <Tag color="blue">{local.template_name}</Tag>
              )}
            </Space>
          ),
          children: (
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Form.Item
                label={t("comp.policyProfile.templateLabel")}
                style={{ marginBottom: 0 }}
              >
                <Select
                  allowClear
                  placeholder={t("comp.policyProfile.templatePlaceholder")}
                  value={local.template_name ?? undefined}
                  onChange={applyTemplate}
                  options={templates.map((tpl) => ({
                    value: tpl.name,
                    label: tpl.name,
                  }))}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t("comp.policyProfile.templateHint")}
                </Text>
              </Form.Item>

              <Form.Item
                label={t("comp.policyProfile.pathsLabel")}
                style={{ marginBottom: 0 }}
              >
                <Input.TextArea
                  rows={3}
                  placeholder={t("comp.policyProfile.pathsPlaceholder")}
                  value={local.allowed_paths.join("\n")}
                  onChange={(e) =>
                    update({
                      allowed_paths: e.target.value
                        .split("\n")
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                />
              </Form.Item>

              <Form.Item
                label={t("comp.policyProfile.bashLabel")}
                style={{ marginBottom: 0 }}
              >
                <Input.TextArea
                  rows={3}
                  placeholder={t("comp.policyProfile.bashPlaceholder")}
                  value={local.allowed_bash_prefixes.join("\n")}
                  onChange={(e) =>
                    update({
                      allowed_bash_prefixes: e.target.value
                        .split("\n")
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                />
              </Form.Item>

              <Form.Item
                label={t("comp.policyProfile.autoApproveLabel")}
                style={{ marginBottom: 0 }}
                tooltip={t("comp.policyProfile.autoApproveTooltip")}
              >
                <Select
                  value={local.auto_approve_max_tier}
                  onChange={(v) => update({ auto_approve_max_tier: v })}
                  options={[
                    { value: 0, label: t("comp.policyProfile.tier0") },
                    { value: 1, label: t("comp.policyProfile.tier1") },
                    { value: 2, label: t("comp.policyProfile.tier2") },
                    { value: 3, label: t("comp.policyProfile.tier3") },
                  ]}
                />
              </Form.Item>

              <Form.Item
                label={t("comp.policyProfile.expiresLabel")}
                style={{ marginBottom: 0 }}
                tooltip={t("comp.policyProfile.expiresTooltip")}
              >
                <InputNumber
                  style={{ width: "100%" }}
                  min={60}
                  placeholder={t("comp.policyProfile.expiresPlaceholder")}
                  value={local.expires_after_seconds ?? undefined}
                  onChange={(v) => update({ expires_after_seconds: v ?? null })}
                />
              </Form.Item>
            </Space>
          ),
        },
      ]}
    />
  );
}
