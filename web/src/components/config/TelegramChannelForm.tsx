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
import {
  getTelegramChannel,
  putTelegramChannel,
  getTelegramStatus,
  listPersonas,
  type TelegramChannelConfig,
} from "../../api/tongzheng";
import { useT } from "../../i18n";
import { isApiProblem } from "../../api/client";

/**
 * Telegram 通道配置表单（与飞书并列，自包含 query/mutation）。
 * 复用 tongzheng.status/action/toast 文案；Telegram 专属文案在 tongzheng.tg.*。
 */
export default function TelegramChannelForm() {
  const t = useT();
  const qc = useQueryClient();
  const [form] = Form.useForm<TelegramChannelConfig>();
  const [secretChanged, setSecretChanged] = useState(false);

  const { data: channel, isLoading } = useQuery({
    queryKey: ["tongzheng", "telegram"],
    queryFn: getTelegramChannel,
  });

  const { data: status } = useQuery({
    queryKey: ["tongzheng", "telegram-status"],
    queryFn: getTelegramStatus,
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
        bot_token: "", // 表单默认空，不显示掩码
      });
      setSecretChanged(false);
    }
  }, [channel, form]);

  const saveMutation = useMutation({
    mutationFn: async (values: TelegramChannelConfig) => {
      const payload: TelegramChannelConfig = {
        ...values,
        bot_token: secretChanged ? values.bot_token : null,
      };
      return await putTelegramChannel(payload);
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
      notification.error({
        message: t("tongzheng.toast.saveFailed"),
        description: isApiProblem(err)
          ? err.message
          : err instanceof Error
            ? err.message
            : String(err),
      });
    },
  });

  const onFinish = (values: TelegramChannelConfig) => {
    saveMutation.mutate(values);
  };

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={onFinish}
      style={{ marginTop: 16 }}
      initialValues={{
        connection_mode: "polling",
        webhook_path: "/channels/telegram/webhook",
        poll_timeout: 30,
        text_batch_delay: 0.6,
        dedup_cache_size: 2048,
        assistant_persona_id: "tongzheng",
        enable_edict_submission: false,
      }}
    >
      <Card
        title={
          <Space>
            <span>{t("tongzheng.tg.section.bot")}</span>
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
          message={t("tongzheng.tg.alert.tipTitle")}
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              <li>{t("tongzheng.tg.alert.tip1")}</li>
              <li>{t("tongzheng.tg.alert.tip2")}</li>
              <li>{t("tongzheng.tg.alert.tip3")}</li>
              <li>{t("tongzheng.tg.alert.tip4")}</li>
            </ul>
          }
          style={{ marginBottom: 16 }}
        />

        <Form.Item
          label={`${t("tongzheng.tg.field.botToken")} ${channel?._has_secret ? t("tongzheng.tg.field.botTokenConfigured") : t("tongzheng.tg.field.botTokenMissing")}`}
          name="bot_token"
          extra={t("tongzheng.tg.field.botTokenExtra")}
        >
          <Input.Password
            placeholder={
              channel?._has_secret
                ? t("tongzheng.tg.placeholder.botTokenConfigured")
                : t("tongzheng.tg.placeholder.botTokenMissing")
            }
            onChange={() => setSecretChanged(true)}
          />
        </Form.Item>

        <Row gutter={16}>
          <Col span={12}>
            <Form.Item label={t("tongzheng.tg.field.connectionMode")} name="connection_mode">
              <Select
                options={[
                  { value: "polling", label: t("tongzheng.tg.option.modePolling") },
                  { value: "webhook", label: t("tongzheng.tg.option.modeWebhook") },
                ]}
              />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label={t("tongzheng.tg.field.webhookPath")} name="webhook_path">
              <Input placeholder="/channels/telegram/webhook" />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item
          label={t("tongzheng.tg.field.allowedUsers")}
          name="allowed_users"
          extra={t("tongzheng.tg.field.allowedUsersExtra")}
        >
          <Input placeholder={t("tongzheng.tg.placeholder.allowedUsers")} />
        </Form.Item>

        <Row gutter={16}>
          <Col span={12}>
            <Form.Item
              label={t("tongzheng.tg.field.homeChannel")}
              name="home_channel"
              extra={t("tongzheng.tg.field.homeChannelExtra")}
            >
              <Input placeholder={t("tongzheng.tg.placeholder.homeChannel")} />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label={t("tongzheng.tg.field.webhookSecret")} name="webhook_secret">
              <Input.Password placeholder={t("tongzheng.tg.placeholder.webhookSecret")} />
            </Form.Item>
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={8}>
            <Form.Item label={t("tongzheng.tg.field.pollTimeout")} name="poll_timeout">
              <InputNumber min={1} max={120} style={{ width: "100%" }} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item label={t("tongzheng.tg.field.textBatchDelay")} name="text_batch_delay">
              <InputNumber min={0} max={5} step={0.1} style={{ width: "100%" }} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item label={t("tongzheng.tg.field.dedupCacheSize")} name="dedup_cache_size">
              <InputNumber min={128} max={65536} style={{ width: "100%" }} />
            </Form.Item>
          </Col>
        </Row>
      </Card>

      <Card
        title={<Space><span>{t("tongzheng.tg.section.assistant")}</span></Space>}
        style={{ marginTop: 16 }}
      >
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
  );
}
