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
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { getAgentConfig, getKeqingStatus, updateAgentConfig } from "../api/config";
import type { AgentConfigUpdateRequest, KeqingBackendStatus } from "../api/types";
import { useT } from "../i18n";
import {
  CapabilityBoundary,
  MaturityBadge,
} from "../components/capabilities/CapabilityMaturity";
import PageContainer from "../components/common/PageContainer";
import PageQueryError from "../components/states/PageQueryError";

const { Paragraph, Text } = Typography;

// 客卿 backend(与后端 adapter 注册一致)+ 各自 provider 的模型示例(仅 placeholder 提示格式)。
const KEQING_BACKENDS = ["pi", "claude-code", "codex", "opencode"] as const;
const KEQING_MODEL_HINTS: Record<string, string> = {
  pi: "zai-coding-cn/glm-4.6",
  "claude-code": "anthropic/claude-opus-4-5",
  codex: "openai/gpt-5",
  opencode: "provider/model",
};

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

  const statusQuery = useQuery({
    queryKey: ["keqing-status"],
    queryFn: getKeqingStatus,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
  });
  const configQuery = useQuery({
    queryKey: ["agent-config"],
    queryFn: getAgentConfig,
  });
  const { data: status, isLoading: statusLoading } = statusQuery;
  const { data: config } = configQuery;

  useEffect(() => {
    if (config) {
      form.setFieldsValue({
        keqing_default_models: config.keqing_default_models ?? {},
        keqing_per_run_budget_cny: config.keqing_per_run_budget_cny ?? 0,
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

  const queryError = statusQuery.error ?? configQuery.error;
  if (queryError) {
    return (
      <PageContainer
        title={t("keqing.title")}
        titleBadge={<MaturityBadge maturity="experimental" />}
      >
        <CapabilityBoundary
          maturity="experimental"
          canDo={t("keqing.capabilityCanDo")}
          boundary={t("keqing.capabilityBoundary")}
        />
        <PageQueryError
          error={queryError}
          onRetry={() => {
            void statusQuery.refetch();
            void configQuery.refetch();
          }}
        />
      </PageContainer>
    );
  }

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
      render: () => <Tag color="blue">{t("keqing.cred.selfManaged")}</Tag>,
    },
  ];

  return (
    <PageContainer
      title={t("keqing.title")}
      titleBadge={<MaturityBadge maturity="experimental" />}
    >
      <CapabilityBoundary
        maturity="experimental"
        canDo={t("keqing.capabilityCanDo")}
        boundary={t("keqing.capabilityBoundary")}
      />
      <Paragraph type="secondary" style={{ marginTop: -8, marginBottom: 16 }}>
        {t("keqing.subtitle")}
      </Paragraph>
      <Space
        direction="vertical"
        size="large"
        style={{ width: "100%", minWidth: 0, maxWidth: "100%" }}
      >
        {/* 1. 健康体检 */}
        <Card
          title={t("keqing.section.registry")}
          size="small"
          style={{ minWidth: 0, maxWidth: "100%" }}
          styles={{ body: { minWidth: 0, overflow: "hidden" } }}
        >
          <Table
            rowKey="id"
            size="small"
            loading={statusLoading}
            dataSource={status?.backends ?? []}
            columns={columns}
            pagination={false}
            scroll={{ x: 720 }}
            style={{ maxWidth: "100%" }}
          />
        </Card>

        {/* 2. 治理默认 */}
        <Card title={t("keqing.section.governance")} size="small">
          <Form
            form={form}
            layout="vertical"
            onFinish={(v) => {
              // 清理 per-客卿默认模型里的空值(空=不配,交客卿自身默认)。
              const raw = (v.keqing_default_models ?? {}) as Record<string, string>;
              const cleaned = Object.fromEntries(
                Object.entries(raw).filter(([, val]) => val && val.trim()),
              );
              mutation.mutate({
                ...v,
                keqing_default_models: cleaned,
              } as AgentConfigUpdateRequest);
            }}
            style={{ maxWidth: 560 }}
          >
            {/* 生效中的治理:凭证自管下也一直有牙(上级机关定模型 + 户部管钱) */}
            <Divider orientation="left" plain style={{ marginTop: 0 }}>
              {t("keqing.group.active")}
            </Divider>
            <Form.Item
              label={t("keqing.field.defaultModel")}
              tooltip={t("keqing.field.defaultModelTip")}
              style={{ marginBottom: 4 }}
            >
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t("keqing.field.defaultModelPerAgentHint")}
              </Text>
            </Form.Item>
            {KEQING_BACKENDS.map((b) => (
              <Form.Item
                key={b}
                name={["keqing_default_models", b]}
                label={b}
                style={{ marginBottom: 8 }}
              >
                <Input
                  placeholder={KEQING_MODEL_HINTS[b] ?? "provider/model:thinking"}
                  allowClear
                />
              </Form.Item>
            ))}
            <Form.Item
              name="keqing_per_run_budget_cny"
              label={t("keqing.field.budget")}
              tooltip={t("keqing.field.budgetTip")}
            >
              <InputNumber min={0} step={1} addonAfter="CNY" style={{ width: 200 }} />
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
    </PageContainer>
  );
}
