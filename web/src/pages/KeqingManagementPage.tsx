import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Divider,
  Form,
  Input,
  InputNumber,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { getAgentConfig, getKeqingStatus, updateAgentConfig } from "../api/config";
import type { AgentConfigUpdateRequest, KeqingBackendStatus } from "../api/types";
import { useT } from "../i18n";

const { Title, Paragraph, Text } = Typography;

/**
 * 客卿管理页 —— 治理「外聘人才」(外部 coding agent)。
 *
 * 客卿是外臣(见 domain-model「执行主体本体论」),不是百官:本页管能力/健康/隔离/治理策略,
 * **不含**人格/京察/自进化。凭证只读展示来源,**无 raw key 输入框**(守凭证隔离)。
 */
export default function KeqingManagementPage() {
  const t = useT();
  const qc = useQueryClient();
  const [form] = Form.useForm();

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ["keqing-status"],
    queryFn: getKeqingStatus,
  });
  const { data: config } = useQuery({
    queryKey: ["agent-config"],
    queryFn: getAgentConfig,
  });

  useEffect(() => {
    if (config) {
      form.setFieldsValue({
        keqing_default_model: config.keqing_default_model ?? "",
        keqing_gateway_enabled: config.keqing_gateway_enabled ?? false,
        keqing_per_run_budget_cny: config.keqing_per_run_budget_cny ?? 0,
        keqing_model_allowlist: config.keqing_model_allowlist ?? "",
      });
    }
  }, [config, form]);

  const mutation = useMutation({
    mutationFn: (req: AgentConfigUpdateRequest) => updateAgentConfig(req),
    onSuccess: () => {
      message.success(t("keqing.saved"));
      qc.invalidateQueries({ queryKey: ["agent-config"] });
      qc.invalidateQueries({ queryKey: ["keqing-status"] });
    },
    onError: () => message.error(t("keqing.saveFailed")),
  });

  const columns: ColumnsType<KeqingBackendStatus> = [
    {
      title: t("keqing.col.backend"),
      dataIndex: "id",
      render: (id: string, row) => (
        <Space>
          <Text strong>{id}</Text>
          {row.backend === "pi" && <Tag color="blue">{t("keqing.default")}</Tag>}
        </Space>
      ),
    },
    {
      title: t("keqing.col.installed"),
      dataIndex: "installed",
      render: (installed: boolean, row) =>
        installed ? (
          <Space direction="vertical" size={0}>
            <Tag color="green">{t("keqing.installedYes")}</Tag>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {row.installed_version ?? "?"}
              {row.pinned_version ? ` / ${t("keqing.pinned")} ${row.pinned_version}` : ""}
            </Text>
            {row.version_drift && <Tag color="red">{t("keqing.drift")}</Tag>}
          </Space>
        ) : (
          <Tag color="default">{t("keqing.installedNo")}</Tag>
        ),
    },
    {
      title: t("keqing.col.capabilities"),
      dataIndex: "capabilities",
      render: (caps: KeqingBackendStatus["capabilities"]) =>
        caps ? (
          <Space size={[4, 4]} wrap>
            {caps.session_resume && <Tag>{t("keqing.cap.resume")}</Tag>}
            {caps.interject && <Tag>{t("keqing.cap.interject")}</Tag>}
            {caps.stop_gate && <Tag>{t("keqing.cap.stopGate")}</Tag>}
            <Tag color={caps.permission_shaping === "none" ? "default" : "purple"}>
              {t("keqing.cap.permission")}: {caps.permission_shaping}
            </Tag>
            <Tag>{t("keqing.cap.usage")}: {caps.usage_reporting}</Tag>
          </Space>
        ) : (
          <Text type="secondary">{t("keqing.cap.singleShot")}</Text>
        ),
    },
    {
      title: t("keqing.col.credential"),
      dataIndex: "credential_status",
      render: (cred: string) => {
        // 客卿=外臣,自管凭证 → 默认「客卿自管」(中性,非错误);开网关 → 「网关托管」。
        const map: Record<string, { color: string; key: string }> = {
          gateway: { color: "green", key: "keqing.cred.gateway" },
          "self-managed": { color: "blue", key: "keqing.cred.selfManaged" },
        };
        const m = map[cred] ?? { color: "blue", key: "keqing.cred.selfManaged" };
        return <Tag color={m.color}>{t(m.key)}</Tag>;
      },
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>
            {t("keqing.title")}
          </Title>
          <Paragraph type="secondary" style={{ marginTop: 4 }}>
            {t("keqing.subtitle")}
          </Paragraph>
        </div>

        {/* 1. 健康体检 */}
        <Card title={t("keqing.section.registry")} size="small">
          <Table
            rowKey="id"
            size="small"
            loading={statusLoading}
            dataSource={status?.backends ?? []}
            columns={columns}
            pagination={false}
          />
        </Card>

        {/* 2. 治理默认 */}
        <Card title={t("keqing.section.governance")} size="small">
          <Form
            form={form}
            layout="vertical"
            onFinish={(v) => mutation.mutate(v as AgentConfigUpdateRequest)}
            style={{ maxWidth: 560 }}
          >
            {/* 生效中的治理:凭证自管下也一直有牙(上级机关定模型 + 户部管钱) */}
            <Divider orientation="left" plain style={{ marginTop: 0 }}>
              {t("keqing.group.active")}
            </Divider>
            <Form.Item
              name="keqing_default_model"
              label={t("keqing.field.defaultModel")}
              tooltip={t("keqing.field.defaultModelTip")}
            >
              <Input placeholder="anthropic/claude-opus-4-5:high" allowClear />
            </Form.Item>
            <Form.Item
              name="keqing_per_run_budget_cny"
              label={t("keqing.field.budget")}
              tooltip={t("keqing.field.budgetTip")}
            >
              <InputNumber min={0} step={1} addonAfter="CNY" style={{ width: 200 }} />
            </Form.Item>

            {/* 网关模式治理:硬管控,须凭证网关接线(P3)后才生效 */}
            <Divider orientation="left" plain>
              {t("keqing.group.gateway")}
            </Divider>
            <Alert
              type="warning"
              showIcon
              message={t("keqing.group.gatewayNote")}
              style={{ marginBottom: 16 }}
            />
            <Form.Item
              name="keqing_gateway_enabled"
              label={t("keqing.field.gateway")}
              tooltip={t("keqing.field.gatewayTip")}
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="keqing_model_allowlist"
              label={t("keqing.field.allowlist")}
              tooltip={t("keqing.field.allowlistTip")}
            >
              <Input placeholder="anthropic/opus, openai/gpt-5" allowClear />
            </Form.Item>

            <Button type="primary" htmlType="submit" loading={mutation.isPending}>
              {t("keqing.save")}
            </Button>
          </Form>
        </Card>

        {/* 3. 凭证说明(只读) */}
        <Alert
          type="info"
          showIcon
          message={t("keqing.cred.title")}
          description={t("keqing.cred.note")}
        />
      </Space>
    </div>
  );
}
