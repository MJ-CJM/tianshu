import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Card,
  Space,
  Typography,
  Row,
  Col,
  Statistic,
  Button,
  Tag,
  Table,
  Empty,
  Tooltip,
  Select,
  Radio,
  Form,
  notification,
} from "antd";
import {
  GlobalOutlined,
  KeyOutlined,
  RightOutlined,
} from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
import { useTools } from "../hooks/useSystem";
import { listNetworkEvents } from "../api/network_events";
import {
  getEngineStatus,
  getEnginePreferences,
  updateEnginePreferences,
} from "../api/hongluisi";
import type { ProviderSource } from "../api/hongluisi";
import type { NetworkEventRow } from "../api/types";
import { formatTime } from "../utils/format";
import { useT } from "../i18n";

const NETWORK_TOOL_NAMES = [
  "web_fetch",
  "web_search",
  "api_request",
  "web_extract",
] as const;

const TOOL_COLORS: Record<string, string> = {
  web_fetch: "blue",
  web_search: "cyan",
  api_request: "geekblue",
  web_extract: "purple",
};

const PROVIDERS_BY_TOOL: Record<string, string[]> = {
  web_fetch: ["jina", "firecrawl"],
  web_search: ["tavily", "jina"],
  api_request: [], // 不走 provider key
  web_extract: ["firecrawl"],
};

export default function HongluisiPage() {
  const t = useT();
  const navigate = useNavigate();
  const { data: tools = [] } = useTools();
  // 权威数据：后端当前真正绑到的 provider key 来源（engine_provider CRUD 后 backend live rebuild）
  const { data: engineStatus } = useQuery({
    queryKey: ["hongluisi", "engine-status"],
    queryFn: getEngineStatus,
    staleTime: 30000,
  });
  const [recent, setRecent] = useState<NetworkEventRow[]>([]);
  const [loading, setLoading] = useState(false);

  // 系统级引擎偏好（live 生效）
  const qc = useQueryClient();
  const { data: prefs } = useQuery({
    queryKey: ["hongluisi", "engine-preferences"],
    queryFn: getEnginePreferences,
    staleTime: 30000,
  });
  const [fetchChain, setFetchChain] = useState<string[]>([]);
  const [searchProvider, setSearchProvider] = useState<string | null>(null);
  const [fallbackMode, setFallbackMode] = useState<string | null>(null);

  useEffect(() => {
    if (prefs) {
      setFetchChain(prefs.fetch_chain);
      setSearchProvider(prefs.search_provider);
      setFallbackMode(prefs.fallback_mode);
    }
  }, [prefs]);

  const saveMutation = useMutation({
    mutationFn: updateEnginePreferences,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hongluisi", "engine-preferences"] });
      notification.success({ message: t("hongluisi.toast.saved") });
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      notification.error({
        message: t("hongluisi.toast.saveFailed"),
        description: String(err?.response?.data?.detail ?? err?.message ?? e),
      });
    },
  });

  useEffect(() => {
    setLoading(true);
    listNetworkEvents({ limit: 20 })
      .then(setRecent)
      .catch(() => setRecent([]))
      .finally(() => setLoading(false));
  }, []);

  // 既要注册、又要没被藏兵阁里 toggle 关闭，才算"已启用"
  const registered = new Set(
    tools
      .filter((t: { enabled?: boolean }) => t.enabled !== false)
      .map((t: { name: string }) => t.name),
  );

  // 权威的 provider → 当前真正用的来源（"db" | "env" | "none"）
  const providerLiveSource: Record<string, ProviderSource> = {
    jina: engineStatus?.providers.jina ?? "none",
    tavily: engineStatus?.providers.tavily ?? "none",
    firecrawl: engineStatus?.providers.firecrawl ?? "none",
  };

  // 返回工具卡片显示的来源：按 db > env > none 优先级合并该工具相关 provider
  const sourceFor = (tool: string): "db" | "env" | "none" => {
    const relevant = PROVIDERS_BY_TOOL[tool] ?? [];
    if (relevant.length === 0) {
      // api_request 不依赖 provider key，依 tool 注册状态判断
      return registered.has(tool) ? "env" : "none";
    }
    const liveSources = relevant.map((p) => providerLiveSource[p]);
    if (liveSources.includes("db")) return "db";
    if (liveSources.includes("env")) return "env";
    return "none";
  };

  const toolCards = NETWORK_TOOL_NAMES.map((name) => {
    const isOn = registered.has(name);

    // api_request 架构不同：不依赖全局 provider key，每次调用从藏兵阁按 host 匹配 Edict 凭证动态注入
    if (name === "api_request") {
      return (
        <Col key={name} xs={12} md={6}>
          <Card size="small">
            <Statistic
              title={name}
              value={isOn ? t("hongluisi.tool.enabled") : t("hongluisi.tool.disabled")}
              valueStyle={{
                color: isOn ? "#52c41a" : "#999",
                fontSize: 18,
              }}
            />
            <Tooltip title={t("hongluisi.tool.apiRequestCredentialTooltip")}>
              <Tag color="purple" style={{ marginTop: 4 }}>
                {t("hongluisi.tool.apiRequestCredentialTag")}
              </Tag>
            </Tooltip>
          </Card>
        </Col>
      );
    }

    const source = sourceFor(name);
    const srcLabel = source === "db" ? "DB" : source === "env" ? "env" : "—";
    const srcColor =
      source === "db" ? "green" : source === "env" ? "blue" : "default";
    return (
      <Col key={name} xs={12} md={6}>
        <Card size="small">
          <Statistic
            title={name}
            value={isOn ? t("hongluisi.tool.enabled") : t("hongluisi.tool.disabled")}
            valueStyle={{
              color: isOn ? "#52c41a" : "#999",
              fontSize: 18,
            }}
          />
          <Space size={4} style={{ marginTop: 4 }} wrap>
            <Tag color={srcColor}>{t("hongluisi.tool.keyPrefix")} {srcLabel}</Tag>
          </Space>
        </Card>
      </Col>
    );
  });

  const columns = [
    {
      title: t("hongluisi.table.time"),
      dataIndex: "created_at",
      key: "created_at",
      width: 160,
      render: (v: string) => formatTime(v),
    },
    {
      title: t("hongluisi.table.tool"),
      dataIndex: "tool",
      key: "tool",
      width: 110,
      render: (v: string) => (
        <Tag color={TOOL_COLORS[v] ?? "default"}>{v}</Tag>
      ),
    },
    {
      title: t("hongluisi.table.host"),
      dataIndex: "host",
      key: "host",
      ellipsis: true,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("hongluisi.table.method"),
      dataIndex: "method",
      key: "method",
      width: 80,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("hongluisi.table.status"),
      dataIndex: "http_status",
      key: "http_status",
      width: 90,
      render: (v: number | null, row: NetworkEventRow) => {
        if (v == null) return row.is_error ? <Tag color="red">error</Tag> : "—";
        const color = v < 400 ? "green" : "red";
        return <Tag color={color}>{v}</Tag>;
      },
    },
    {
      title: t("hongluisi.table.credential"),
      dataIndex: "credential_name",
      key: "credential_name",
      width: 160,
      render: (v: string | null) => v ?? "—",
    },
  ];

  return (
    <PageContainer title={t("hongluisi.title")}>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Card
          title={
            <Space>
              <GlobalOutlined />
              <span>{t("hongluisi.section.tools")}</span>
            </Space>
          }
          size="small"
        >
          <Row gutter={[12, 12]}>{toolCards}</Row>
          <Typography.Paragraph
            type="secondary"
            style={{ marginTop: 12, marginBottom: 0, fontSize: 12 }}
          >
            {t("hongluisi.toolsHint1")}
            <code>TIANSHU_SECRET_MASTER_KEY</code>{t("hongluisi.toolsHint2")}
            <code>TIANSHU_FIRECRAWL_API_KEY</code>{t("hongluisi.toolsHint3")}
          </Typography.Paragraph>
        </Card>

        <Card
          title={
            <Space>
              <span>{t("hongluisi.section.preferences")}</span>
            </Space>
          }
          size="small"
          extra={
            <Button
              type="primary"
              loading={saveMutation.isPending}
              onClick={() =>
                saveMutation.mutate({
                  fetch_chain: fetchChain,
                  search_provider: searchProvider,
                  fallback_mode: fallbackMode,
                })
              }
            >
              {t("hongluisi.preferences.save")}
            </Button>
          }
        >
          <Space direction="vertical" style={{ width: "100%" }} size="middle">
            <Typography.Paragraph
              type="secondary"
              style={{ marginBottom: 0, fontSize: 12 }}
            >
              {t("hongluisi.preferences.intro")}
            </Typography.Paragraph>

            <Form.Item
              label={t("hongluisi.preferences.fetchChainLabel")}
              style={{ marginBottom: 0 }}
            >
              <Select
                mode="multiple"
                placeholder={t("hongluisi.preferences.fetchChainPlaceholder")}
                value={fetchChain}
                onChange={setFetchChain}
                options={[
                  { value: "local", label: "local (trafilatura)" },
                  { value: "jina", label: "jina (r.jina.ai)" },
                  { value: "firecrawl", label: "firecrawl" },
                ]}
                style={{ width: "100%" }}
              />
            </Form.Item>

            <Form.Item
              label={t("hongluisi.preferences.fallbackLabel")}
              style={{ marginBottom: 0 }}
            >
              <Radio.Group
                value={fallbackMode ?? ""}
                onChange={(e) => setFallbackMode(e.target.value || null)}
              >
                <Radio value="">{t("hongluisi.preferences.fallbackProfile")}</Radio>
                <Radio value="on_error_or_empty">{t("hongluisi.preferences.fallbackOnErrorOrEmpty")}</Radio>
                <Radio value="none">{t("hongluisi.preferences.fallbackNone")}</Radio>
              </Radio.Group>
            </Form.Item>

            <Form.Item label={t("hongluisi.preferences.searchLabel")} style={{ marginBottom: 0 }}>
              <Radio.Group
                value={searchProvider ?? ""}
                onChange={(e) => setSearchProvider(e.target.value || null)}
              >
                <Radio value="">{t("hongluisi.preferences.fallbackProfile")}</Radio>
                <Radio value="tavily">Tavily</Radio>
                <Radio value="jina">Jina Search</Radio>
              </Radio.Group>
            </Form.Item>
          </Space>
        </Card>

        <Card
          title={
            <Space>
              <KeyOutlined />
              <span>{t("hongluisi.section.credentials")}</span>
            </Space>
          }
          size="small"
          extra={
            <Button
              type="link"
              onClick={() => navigate("/system?tab=external-creds")}
            >
              {t("hongluisi.credentials.goto")} <RightOutlined />
            </Button>
          }
        >
          <Typography.Paragraph style={{ marginBottom: 0 }}>
            {t("hongluisi.credentials.desc1")}
            <strong>{t("hongluisi.credentials.desc2")}</strong>
            {t("hongluisi.credentials.desc3")}
          </Typography.Paragraph>
        </Card>

        <Card
          title={t("hongluisi.section.recent")}
          size="small"
          extra={
            <Button type="link" onClick={() => navigate("/audit?tab=network")}>
              {t("hongluisi.recent.viewAll")} <RightOutlined />
            </Button>
          }
        >
          <Table<NetworkEventRow>
            rowKey="event_id"
            columns={columns}
            dataSource={recent}
            loading={loading}
            pagination={false}
            size="small"
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={t("hongluisi.recent.empty")}
                />
              ),
            }}
          />
        </Card>
      </Space>
    </PageContainer>
  );
}
