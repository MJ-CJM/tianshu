import { useEffect, useState } from "react";
import {
  Card,
  Form,
  Input,
  Select,
  InputNumber,
  Button,
  Space,
  Switch,
  Tag,
  Alert,
  Row,
  Col,
  notification,
} from "antd";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ReloadOutlined } from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
import {
  getFeishuChannel,
  putFeishuChannel,
  getFeishuStatus,
  listPersonas,
  type FeishuChannelConfig,
} from "../api/tongzheng";
import { useT } from "../i18n";

export default function TongzhengPage() {
  const t = useT();
  const qc = useQueryClient();
  const [form] = Form.useForm<FeishuChannelConfig>();
  const [secretChanged, setSecretChanged] = useState(false);

  const { data: channel, isLoading } = useQuery({
    queryKey: ["tongzheng", "feishu"],
    queryFn: getFeishuChannel,
  });

  const { data: status } = useQuery({
    queryKey: ["tongzheng", "feishu-status"],
    queryFn: getFeishuStatus,
    refetchInterval: 15_000,
  });

  const { data: personas } = useQuery({
    queryKey: ["tongzheng", "personas"],
    queryFn: listPersonas,
  });

  useEffect(() => {
    if (channel) {
      form.setFieldsValue({
        ...channel,
        app_secret: "", // 表单里默认空，不显示掩码
      });
      setSecretChanged(false);
    }
  }, [channel, form]);

  const saveMutation = useMutation({
    mutationFn: async (values: FeishuChannelConfig) => {
      // app_secret 处理：用户没改 → 传 null；改了 → 传输入值（含空串=清空）
      const payload: FeishuChannelConfig = {
        ...values,
        app_secret: secretChanged ? values.app_secret : null,
      };
      return await putFeishuChannel(payload);
    },
    onSuccess: (result) => {
      notification.success({
        message: t("tongzheng.toast.saved"),
        description: result.reloaded
          ? t("tongzheng.toast.savedReloaded")
          : t("tongzheng.toast.savedNoReload", { reason: result.reason ?? "" }),
        duration: 5,
      });
      qc.invalidateQueries({ queryKey: ["tongzheng"] });
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } }; message?: string };
      notification.error({
        message: t("tongzheng.toast.saveFailed"),
        description: e?.response?.data?.detail ?? e?.message ?? String(err),
      });
    },
  });

  const onFinish = (values: FeishuChannelConfig) => {
    saveMutation.mutate(values);
  };

  return (
    <PageContainer title={t("tongzheng.title")}>
      <Form
        form={form}
        layout="vertical"
        onFinish={onFinish}
        initialValues={{
          domain: "feishu",
          connection_mode: "websocket",
          webhook_path: "/feishu/webhook",
          ws_reconnect_interval: 120,
          text_batch_delay: 0.6,
          dedup_cache_size: 2048,
          assistant_persona_id: "tongzheng",
          intent_llm_enabled: true,
          enable_edict_submission: false,
        }}
      >
        <Card
          title={
            <Space>
              <span>{t("tongzheng.section.bot")}</span>
              {status?.running ? (
                <Tag color="green">{t("tongzheng.status.running", { mode: status.mode ?? "" })}</Tag>
              ) : (
                <Tag>{t("tongzheng.status.off")}</Tag>
              )}
              {channel?._source === "env" && (
                <Tag color="orange">{t("tongzheng.status.fromEnv")}</Tag>
              )}
              {channel?._source === "db" && <Tag color="blue">{t("tongzheng.status.fromDb")}</Tag>}
            </Space>
          }
          loading={isLoading}
        >
          <Alert
            type="info"
            showIcon
            message={t("tongzheng.alert.tipTitle")}
            description={
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                <li>
                  {t("tongzheng.alert.tip1Prefix")}<code>TIANSHU_SECRET_MASTER_KEY</code>{t("tongzheng.alert.tip1Suffix")}
                </li>
                <li>
                  {t("tongzheng.alert.tip2Prefix")}<code>app_secret</code>{t("tongzheng.alert.tip2Suffix")}
                </li>
                <li>{t("tongzheng.alert.tip3")}</li>
                <li>
                  {t("tongzheng.alert.tip4Prefix")}<code>docs/ops/feishu-setup.md</code>{t("tongzheng.alert.tip4Suffix")}
                </li>
              </ul>
            }
            style={{ marginBottom: 16 }}
          />
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label={t("tongzheng.field.appId")}
                name="app_id"
                rules={[{ required: true }]}
              >
                <Input placeholder="cli_xxxxxxxxx" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label={`${t("tongzheng.field.appSecret")} ${channel?._has_secret ? t("tongzheng.field.appSecretConfigured") : t("tongzheng.field.appSecretMissing")}`}
                name="app_secret"
                extra={t("tongzheng.field.appSecretExtra")}
              >
                <Input.Password
                  placeholder={
                    channel?._has_secret
                      ? t("tongzheng.placeholder.appSecretConfigured")
                      : t("tongzheng.placeholder.appSecretMissing")
                  }
                  onChange={() => setSecretChanged(true)}
                />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label={t("tongzheng.field.domain")} name="domain">
                <Select
                  options={[
                    { value: "feishu", label: t("tongzheng.option.domainFeishu") },
                    { value: "lark", label: t("tongzheng.option.domainLark") },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label={t("tongzheng.field.connectionMode")} name="connection_mode">
                <Select
                  options={[
                    {
                      value: "websocket",
                      label: t("tongzheng.option.modeWebsocket"),
                    },
                    { value: "webhook", label: t("tongzheng.option.modeWebhook") },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label={t("tongzheng.field.webhookPath")} name="webhook_path">
                <Input placeholder="/feishu/webhook" />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            label={t("tongzheng.field.allowedUsers")}
            name="allowed_users"
            extra={t("tongzheng.field.allowedUsersExtra")}
          >
            <Input placeholder={t("tongzheng.placeholder.allowedUsers")} />
          </Form.Item>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label={t("tongzheng.field.homeChannel")}
                name="home_channel"
                extra={t("tongzheng.field.homeChannelExtra")}
              >
                <Input placeholder={t("tongzheng.placeholder.homeChannel")} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label={t("tongzheng.field.botOpenId")}
                name="bot_open_id"
              >
                <Input placeholder={t("tongzheng.placeholder.botOpenId")} />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label={t("tongzheng.field.encryptKey")} name="encrypt_key">
                <Input.Password placeholder={t("tongzheng.placeholder.encryptKey")} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label={t("tongzheng.field.verificationToken")}
                name="verification_token"
              >
                <Input.Password placeholder={t("tongzheng.placeholder.verificationToken")} />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={8}>
              <Form.Item
                label={t("tongzheng.field.wsReconnect")}
                name="ws_reconnect_interval"
              >
                <InputNumber min={30} max={600} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                label={t("tongzheng.field.textBatchDelay")}
                name="text_batch_delay"
              >
                <InputNumber
                  min={0}
                  max={5}
                  step={0.1}
                  style={{ width: "100%" }}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label={t("tongzheng.field.dedupCacheSize")} name="dedup_cache_size">
                <InputNumber
                  min={128}
                  max={65536}
                  style={{ width: "100%" }}
                />
              </Form.Item>
            </Col>
          </Row>

        </Card>

        <Card
          title={
            <Space>
              <span>{t("tongzheng.section.assistant")}</span>
            </Space>
          }
          style={{ marginTop: 16 }}
        >
          <Alert
            type="info"
            showIcon
            message={t("tongzheng.alert.assistantTitle")}
            description={
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                <li>{t("tongzheng.alert.assistant1")}</li>
                <li>{t("tongzheng.alert.assistant2")}</li>
              </ul>
            }
            style={{ marginBottom: 16 }}
          />

          <Form.Item
            label={t("tongzheng.field.assistantPersona")}
            name="assistant_persona_id"
            extra={t("tongzheng.field.assistantPersonaExtra")}
          >
            <Select
              placeholder={t("tongzheng.placeholder.assistantPersona")}
              options={(personas ?? []).map((p) => ({
                value: p.id,
                label: `${p.name} (${p.department})`,
              }))}
            />
          </Form.Item>

          <Form.Item
            label={t("tongzheng.field.enableEdict")}
            name="enable_edict_submission"
            valuePropName="checked"
            extra={t("tongzheng.field.enableEdictExtra")}
          >
            <Switch />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 16 }}>
            <Space>
              <Button
                type="primary"
                htmlType="submit"
                icon={<ReloadOutlined />}
                loading={saveMutation.isPending}
              >
                {t("tongzheng.action.save")}
              </Button>
              <Button onClick={() => form.resetFields()}>{t("tongzheng.action.reset")}</Button>
            </Space>
          </Form.Item>
        </Card>
      </Form>
    </PageContainer>
  );
}
