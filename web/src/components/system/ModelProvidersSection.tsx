import { useState } from "react";
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  notification,
  theme,
} from "antd";
import { DeleteOutlined, PlusOutlined, SyncOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import type { DefaultOptionType } from "antd/es/select";
import {
  useCatalogStatus,
  useCreateModelProvider,
  useDeleteModelProvider,
  useModelProviderProfiles,
  useModelProviders,
  useRefreshCatalog,
  useSetModelProviderKey,
  useTestModelProvider,
  useUpdateModelProvider,
} from "../../hooks/useModelProviders";
import type {
  ModelProviderCreateRequest,
  ModelProviderView,
} from "../../api/types";
import ModelSelect from "./ModelSelect";
import { useT } from "../../i18n";
import PageQueryError from "../states/PageQueryError";

/** 「模型供应商」区块：目录状态 + provider 实例表 + 添加向导 / 录 key / 连通测试。 */

function formatGeneratedAt(raw: string): string {
  const d = dayjs(raw);
  return d.isValid() ? d.format("YYYY-MM-DD HH:mm") : raw;
}

function KeySourceCell({ record }: { record: ModelProviderView }) {
  const t = useT();
  if (record.key_source === "vault") {
    return (
      <Space size={4}>
        <Tag color="green">{t("system.providers.registry.keyVault")}</Tag>
        <span style={{ fontSize: 12, fontFamily: "monospace" }}>
          {record.key_masked}
        </span>
      </Space>
    );
  }
  if (record.key_source === "env") {
    return (
      <Tooltip title={record.key_env || record.api_key_ref}>
        <Tag color="blue">{t("system.providers.registry.keyEnv")}</Tag>
      </Tooltip>
    );
  }
  return <Tag>{t("system.providers.registry.keyNone")}</Tag>;
}

/** 连通测试 Modal 内容（配合 destroyOnClose，每次打开重置状态） */
function TestModalBody({ provider }: { provider: ModelProviderView }) {
  const t = useT();
  const [model, setModel] = useState("");
  const testMutation = useTestModelProvider();
  const result = testMutation.data;

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <ModelSelect
        providerId={provider.id}
        value={model}
        onChange={setModel}
        placeholder={t("system.providers.registry.testModelPlaceholder")}
      />
      <Button
        type="primary"
        size="small"
        disabled={!model.trim()}
        loading={testMutation.isPending}
        onClick={() =>
          testMutation.mutate({ id: provider.id, model: model.trim() })
        }
      >
        {t("system.providers.registry.testRun")}
      </Button>
      {result &&
        (result.ok ? (
          <Alert
            type="success"
            showIcon
            message={t("system.providers.registry.testOk", {
              latency: result.latency_ms,
            })}
          />
        ) : (
          <Alert
            type="error"
            showIcon
            message={t("system.providers.registry.testFail")}
            description={result.error ?? undefined}
          />
        ))}
    </Space>
  );
}

/** 添加供应商向导 Modal 内容（Form 部分） */
function AddProviderModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const t = useT();
  const { token } = theme.useToken();
  const profilesQuery = useModelProviderProfiles();
  const { data: profiles } = profilesQuery;
  const createMutation = useCreateModelProvider();
  const [form] = Form.useForm<ModelProviderCreateRequest>();
  const profileId = Form.useWatch("profile_id", form);
  const selectedProfile = profiles?.find((p) => p.id === profileId);
  const isCustom = profileId === "custom";

  const profileOptions: DefaultOptionType[] = (profiles ?? []).map((p) => ({
    value: p.id,
    searchText: `${p.id} ${p.display_name}`.toLowerCase(),
    label: (
      <Space size={4}>
        {p.display_name}
        {p.billing === "subscription" && (
          <Tag color="purple" style={{ fontSize: 11 }}>
            {t("system.providers.registry.billingSubscription")}
          </Tag>
        )}
      </Space>
    ),
  }));

  const handleOk = () => {
    form.validateFields().then((values) => {
      createMutation.mutate(values, {
        onSuccess: (created) => {
          notification.success({
            message: t("system.providers.registry.created", {
              id: created.id,
            }),
          });
          onClose();
          form.resetFields();
        },
      });
    });
  };

  return (
    <Modal
      title={t("system.providers.registry.addTitle")}
      open={open}
      onOk={handleOk}
      onCancel={() => {
        onClose();
        form.resetFields();
      }}
      confirmLoading={createMutation.isPending}
      okButtonProps={{ disabled: Boolean(profilesQuery.error) }}
      okText={t("action.add")}
      cancelText={t("common.cancel")}
    >
      {profilesQuery.error ? (
        <PageQueryError
          error={profilesQuery.error}
          onRetry={() => void profilesQuery.refetch()}
        />
      ) : (
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item
          name="profile_id"
          label={t("system.providers.registry.form.profile")}
          rules={[
            {
              required: true,
              message: t("system.providers.registry.form.profileRequired"),
            },
          ]}
        >
          <Select
            showSearch
            placeholder={t("system.providers.registry.form.profilePlaceholder")}
            options={profileOptions}
            filterOption={(input, option) =>
              String(option?.searchText ?? "").includes(input.toLowerCase())
            }
          />
        </Form.Item>
        {selectedProfile && (
          <div
            style={{
              fontSize: 12,
              color: token.colorTextTertiary,
              marginTop: -16,
              marginBottom: 12,
            }}
          >
            {selectedProfile.key_env && (
              <div>
                {t("system.providers.registry.form.keyEnvHint", {
                  env: selectedProfile.key_env,
                })}
              </div>
            )}
            {selectedProfile.notes && <div>{selectedProfile.notes}</div>}
          </div>
        )}
        <Form.Item name="id" label={t("system.providers.registry.form.id")}>
          <Input
            placeholder={t("system.providers.registry.form.idPlaceholder", {
              id: selectedProfile?.id ?? "profile id",
            })}
          />
        </Form.Item>
        <Form.Item
          name="display_name"
          label={t("system.providers.registry.form.displayName")}
        >
          <Input
            placeholder={t(
              "system.providers.registry.form.displayNamePlaceholder",
            )}
          />
        </Form.Item>
        <Form.Item
          name="base_url"
          label="Base URL"
          rules={
            isCustom
              ? [
                  {
                    required: true,
                    whitespace: true,
                    message: t(
                      "system.providers.registry.form.baseUrlRequired",
                    ),
                  },
                ]
              : []
          }
        >
          <Input
            placeholder={
              selectedProfile?.default_base_url ||
              t("system.providers.registry.form.baseUrlPlaceholder")
            }
          />
        </Form.Item>
        <Form.Item name="api_key" label="API Key">
          <Input.Password
            placeholder={t("system.providers.registry.form.apiKeyPlaceholder")}
          />
        </Form.Item>
      </Form>
      )}
    </Modal>
  );
}

export default function ModelProvidersSection() {
  const t = useT();
  const { token } = theme.useToken();
  const { data: providers, isLoading } = useModelProviders();
  const { data: catalogStatus } = useCatalogStatus();
  const refreshMutation = useRefreshCatalog();
  const updateMutation = useUpdateModelProvider();
  const setKeyMutation = useSetModelProviderKey();
  const deleteMutation = useDeleteModelProvider();

  const [addOpen, setAddOpen] = useState(false);
  const [keyTarget, setKeyTarget] = useState<ModelProviderView | null>(null);
  const [keyValue, setKeyValue] = useState("");
  const [testTarget, setTestTarget] = useState<ModelProviderView | null>(null);

  const handleRefreshCatalog = () => {
    refreshMutation.mutate(undefined, {
      onSuccess: (status) => {
        notification.success({
          message: t("system.providers.registry.catalogRefreshed", {
            generatedAt: formatGeneratedAt(status.generated_at),
          }),
        });
      },
    });
  };

  const handleSaveKey = () => {
    if (!keyTarget) return;
    const cleared = keyValue.trim() === "";
    setKeyMutation.mutate(
      { id: keyTarget.id, apiKey: keyValue },
      {
        onSuccess: () => {
          notification.success({
            message: cleared
              ? t("system.providers.registry.keyCleared")
              : t("system.providers.registry.keySaved"),
          });
          setKeyTarget(null);
          setKeyValue("");
        },
      },
    );
  };

  const columns: import("antd/es/table").ColumnsType<ModelProviderView> = [
    { title: "ID", dataIndex: "id", key: "id", width: 130 },
    {
      title: t("system.providers.registry.table.displayName"),
      dataIndex: "display_name",
      key: "display_name",
      width: 140,
    },
    {
      title: t("system.providers.registry.table.profile"),
      dataIndex: "profile_id",
      key: "profile_id",
      width: 110,
    },
    {
      title: t("system.providers.registry.table.endpoint"),
      dataIndex: "effective_base_url",
      key: "endpoint",
      ellipsis: { showTitle: false },
      render: (v: string) => (
        <Tooltip title={v}>
          <span style={{ fontSize: 12 }}>{v}</span>
        </Tooltip>
      ),
    },
    {
      title: t("system.providers.registry.table.keyStatus"),
      key: "key",
      width: 170,
      render: (_, record) => <KeySourceCell record={record} />,
    },
    {
      title: t("system.providers.registry.table.billing"),
      dataIndex: "billing",
      key: "billing",
      width: 80,
      render: (v: string) =>
        v === "subscription" ? (
          <Tag color="purple">
            {t("system.providers.registry.billingSubscription")}
          </Tag>
        ) : (
          <span style={{ fontSize: 12, color: token.colorTextTertiary }}>
            {t("system.providers.registry.billingPerToken")}
          </span>
        ),
    },
    {
      title: t("system.providers.registry.table.enabled"),
      dataIndex: "enabled",
      key: "enabled",
      width: 70,
      render: (v: boolean, record) => (
        <Switch
          size="small"
          checked={v}
          onChange={(checked) =>
            updateMutation.mutate({ id: record.id, req: { enabled: checked } })
          }
        />
      ),
    },
    {
      title: "",
      key: "actions",
      width: 190,
      render: (_, record) => (
        <Space size={4}>
          <Button
            size="small"
            onClick={() => {
              setKeyValue("");
              setKeyTarget(record);
            }}
          >
            {t("system.providers.registry.actionKey")}
          </Button>
          <Button size="small" onClick={() => setTestTarget(record)}>
            {t("system.providers.registry.actionTest")}
          </Button>
          <Popconfirm
            title={t("system.providers.registry.confirmDelete")}
            onConfirm={() =>
              deleteMutation.mutate(record.id, {
                onSuccess: () =>
                  notification.success({
                    message: t("system.providers.registry.deleted", {
                      id: record.id,
                    }),
                  }),
              })
            }
            okText={t("action.delete")}
            cancelText={t("common.cancel")}
          >
            <Button type="text" danger size="small" icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 4,
        }}
      >
        <Typography.Title level={5} style={{ margin: 0 }}>
          {t("system.providers.registry.title")}
        </Typography.Title>
        <Space size={8}>
          <Button
            size="small"
            icon={<SyncOutlined />}
            loading={refreshMutation.isPending}
            onClick={handleRefreshCatalog}
          >
            {t("system.providers.registry.refreshCatalog")}
          </Button>
          <Button
            type="primary"
            size="small"
            icon={<PlusOutlined />}
            onClick={() => setAddOpen(true)}
          >
            {t("system.providers.registry.addProvider")}
          </Button>
        </Space>
      </div>
      {catalogStatus && (
        <div
          style={{
            fontSize: 12,
            color: token.colorTextTertiary,
            marginBottom: 12,
          }}
        >
          {t("system.providers.registry.catalogStatus", {
            providerCount: catalogStatus.provider_count,
            modelCount: catalogStatus.model_count,
            generatedAt: formatGeneratedAt(catalogStatus.generated_at),
            source: catalogStatus.source,
          })}
        </div>
      )}
      <Table<ModelProviderView>
        columns={columns}
        dataSource={providers ?? []}
        rowKey="id"
        loading={isLoading}
        size="small"
        pagination={false}
        locale={{ emptyText: t("system.providers.registry.empty") }}
      />

      <AddProviderModal open={addOpen} onClose={() => setAddOpen(false)} />

      {/* 录入 Key Modal */}
      <Modal
        title={t("system.providers.registry.keyModalTitle", {
          name: keyTarget?.display_name ?? keyTarget?.id ?? "",
        })}
        open={!!keyTarget}
        onOk={handleSaveKey}
        onCancel={() => {
          setKeyTarget(null);
          setKeyValue("");
        }}
        confirmLoading={setKeyMutation.isPending}
        okText={t("common.confirm")}
        cancelText={t("common.cancel")}
        width={420}
      >
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          <Input.Password
            value={keyValue}
            onChange={(e) => setKeyValue(e.target.value)}
            placeholder={t(
              "system.providers.registry.form.apiKeyPlaceholder",
            )}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("system.providers.registry.keyHint")}
          </Typography.Text>
        </Space>
      </Modal>

      {/* 连通测试 Modal */}
      <Modal
        title={t("system.providers.registry.testModalTitle", {
          name: testTarget?.display_name ?? testTarget?.id ?? "",
        })}
        open={!!testTarget}
        onCancel={() => setTestTarget(null)}
        footer={null}
        width={420}
        destroyOnClose
      >
        {testTarget && <TestModalBody provider={testTarget} />}
      </Modal>
    </>
  );
}
