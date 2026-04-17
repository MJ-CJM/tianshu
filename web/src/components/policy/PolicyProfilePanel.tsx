import { useEffect, useState } from "react";
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
  const [templates, setTemplates] = useState<PolicyTemplate[]>([]);
  const [local, setLocal] = useState<PolicyProfileValue>(value ?? DEFAULT_VALUE);

  useEffect(() => {
    fetchPolicyTemplates()
      .then(setTemplates)
      .catch(() => setTemplates([]));
  }, []);

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

  return (
    <Collapse
      ghost
      items={[
        {
          key: "policy",
          label: (
            <Space>
              <SafetyOutlined />
              <span>工具权限策略（可选）</span>
              {local.template_name && (
                <Tag color="blue">{local.template_name}</Tag>
              )}
            </Space>
          ),
          children: (
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Form.Item label="预设模板" style={{ marginBottom: 0 }}>
                <Select
                  allowClear
                  placeholder="选择预设模板（可选）"
                  value={local.template_name ?? undefined}
                  onChange={applyTemplate}
                  options={templates.map((t) => ({
                    value: t.name,
                    label: t.name,
                  }))}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  选择模板后可进一步自定义
                </Text>
              </Form.Item>

              <Form.Item label="允许路径 (glob)" style={{ marginBottom: 0 }}>
                <Input.TextArea
                  rows={3}
                  placeholder={"每行一条，例如：\nsrc/**\ntests/**"}
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

              <Form.Item label="允许 Bash 前缀" style={{ marginBottom: 0 }}>
                <Input.TextArea
                  rows={3}
                  placeholder={"每行一条，例如：\ngit status\nls\npytest"}
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
                label="自动放行最高 Tier"
                style={{ marginBottom: 0 }}
                tooltip="Tier ≤ 该值的工具将自动放行，不需要审批"
              >
                <Select
                  value={local.auto_approve_max_tier}
                  onChange={(v) => update({ auto_approve_max_tier: v })}
                  options={[
                    { value: 0, label: "T0 只读" },
                    { value: 1, label: "T1 工作区" },
                    { value: 2, label: "T2 写操作" },
                    { value: 3, label: "T3 危险" },
                  ]}
                />
              </Form.Item>

              <Form.Item
                label="规则有效期 (秒)"
                style={{ marginBottom: 0 }}
                tooltip="留空表示永久有效（直到敕令结束）"
              >
                <InputNumber
                  style={{ width: "100%" }}
                  min={60}
                  placeholder="留空 = 永久"
                  value={local.expires_after_seconds ?? undefined}
                  onChange={(v) =>
                    update({ expires_after_seconds: v ?? null })
                  }
                />
              </Form.Item>
            </Space>
          ),
        },
      ]}
    />
  );
}
