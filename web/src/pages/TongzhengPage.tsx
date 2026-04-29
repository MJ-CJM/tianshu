import { useEffect, useState } from "react";
import {
  Card,
  Form,
  Input,
  Select,
  InputNumber,
  Switch,
  Button,
  Space,
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

export default function TongzhengPage() {
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
        message: "已保存",
        description: result.reloaded
          ? "配置已保存并热加载完成"
          : `配置已保存（${result.reason}）`,
        duration: 5,
      });
      qc.invalidateQueries({ queryKey: ["tongzheng"] });
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } }; message?: string };
      notification.error({
        message: "保存失败",
        description: e?.response?.data?.detail ?? e?.message ?? String(err),
      });
    },
  });

  const onFinish = (values: FeishuChannelConfig) => {
    saveMutation.mutate(values);
  };

  return (
    <PageContainer title="通政司">
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
        }}
      >
        <Card
          title={
            <Space>
              <span>飞书机器人</span>
              {status?.running ? (
                <Tag color="green">运行中（{status.mode}）</Tag>
              ) : (
                <Tag>未启用</Tag>
              )}
              {channel?._source === "env" && (
                <Tag color="orange">来自环境变量（保存后切换为 DB）</Tag>
              )}
              {channel?._source === "db" && <Tag color="blue">来自 DB</Tag>}
            </Space>
          }
          loading={isLoading}
        >
          <Alert
            type="info"
            showIcon
            message="提示"
            description={
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                <li>
                  保存配置需要先设置环境变量 <code>TIANSHU_SECRET_MASTER_KEY</code>
                  （用 cryptography.fernet 生成）
                </li>
                <li>
                  修改 <code>app_secret</code> 后保存才会写入 DB；空着保存 = 不修改
                </li>
                <li>保存后自动热加载，无需重启服务</li>
                <li>
                  需先在飞书开发者后台开启「机器人」「事件订阅」「卡片回调」等能力，详见{" "}
                  <code>docs/ops/feishu-setup.md</code>
                </li>
              </ul>
            }
            style={{ marginBottom: 16 }}
          />
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="App ID"
                name="app_id"
                rules={[{ required: true }]}
              >
                <Input placeholder="cli_xxxxxxxxx" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label={`App Secret ${channel?._has_secret ? "（已配置）" : "（未配置）"}`}
                name="app_secret"
                extra="留空 = 不修改；填新值 = 替换；空字符串 = 清空"
              >
                <Input.Password
                  placeholder={
                    channel?._has_secret
                      ? "已存储，留空不修改"
                      : "secret_xxx"
                  }
                  onChange={() => setSecretChanged(true)}
                />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="域" name="domain">
                <Select
                  options={[
                    { value: "feishu", label: "feishu (China)" },
                    { value: "lark", label: "lark (国际)" },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="连接模式" name="connection_mode">
                <Select
                  options={[
                    {
                      value: "websocket",
                      label: "WebSocket（推荐，无需公网）",
                    },
                    { value: "webhook", label: "Webhook（HTTP 回调）" },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="Webhook 路径" name="webhook_path">
                <Input placeholder="/feishu/webhook" />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            label="允许用户（Allowlist）"
            name="allowed_users"
            extra="逗号分隔的 open_id；留空 = 任何人都放行（与 hermes 一致）。生产环境建议填写以避免误开放。"
          >
            <Input placeholder="留空 = 放行任意；或填 ou_a,ou_b 限制" />
          </Form.Item>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="Home 频道（可选）"
                name="home_channel"
                extra="cron 触发结果与无源敕令审批的兜底 chat_id"
              >
                <Input placeholder="oc_xxxxxxxxx" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="机器人 Open ID（群 @ 检测）"
                name="bot_open_id"
              >
                <Input placeholder="ou_bot_xxx" />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="加密密钥（Webhook 模式）" name="encrypt_key">
                <Input.Password placeholder="可选" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="校验 Token（Webhook 模式）"
                name="verification_token"
              >
                <Input.Password placeholder="可选" />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={8}>
              <Form.Item
                label="WS 重连间隔（秒）"
                name="ws_reconnect_interval"
              >
                <InputNumber min={30} max={600} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                label="文本批处理静默期（秒）"
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
              <Form.Item label="消息去重缓存大小" name="dedup_cache_size">
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
              <span>飞书助手</span>
            </Space>
          }
          style={{ marginTop: 16 }}
        >
          <Alert
            type="info"
            showIcon
            message="助手是飞书侧的命令路由 + 自然语言意图层"
            description={
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                <li>
                  选一个 cabinet persona 兼任飞书助手，借用其名字 + emoji 作为回信人格
                </li>
                <li>
                  启用 LLM 意图增强后，纯文本（如"显示列表"）可解析为命令；每条非命令消息会调一次 persona 的 LLM
                </li>
              </ul>
            }
            style={{ marginBottom: 16 }}
          />

          <Form.Item
            label="助手 Persona"
            name="assistant_persona_id"
            extra="助手用此 persona 的人格渲染回信；不影响该 persona 原本的敕令任务"
          >
            <Select
              placeholder="选择一个 persona"
              options={(personas ?? []).map((p) => ({
                value: p.id,
                label: `${p.name}（${p.department}）`,
              }))}
            />
          </Form.Item>

          <Form.Item
            label="启用 LLM 意图增强"
            name="intent_llm_enabled"
            valuePropName="checked"
            extra='开启后，自然语言（如"显示我的列表"）会过 persona 的 LLM 解析为命令'
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
                保存并热加载
              </Button>
              <Button onClick={() => form.resetFields()}>重置</Button>
            </Space>
          </Form.Item>
        </Card>
      </Form>
    </PageContainer>
  );
}
