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
  ExclamationCircleOutlined,
} from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
import { useTools } from "../hooks/useSystem";
import { listNetworkEvents } from "../api/network_events";
import { listCredentials } from "../api/credentials";
import {
  getEngineStatus,
  getEnginePreferences,
  updateEnginePreferences,
} from "../api/hongluisi";
import type { ProviderSource } from "../api/hongluisi";
import type { NetworkEventRow } from "../api/types";
import { formatTime } from "../utils/format";

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
  const navigate = useNavigate();
  const { data: tools = [] } = useTools();
  // 权威数据：后端启动时真正绑到的 provider key 来源
  const { data: engineStatus } = useQuery({
    queryKey: ["hongluisi", "engine-status"],
    queryFn: getEngineStatus,
    staleTime: 30000,
  });
  // DB 当前记录：用于检测"已改但未重启"
  const { data: providers = [] } = useQuery({
    queryKey: ["credentials", "engine_provider"],
    queryFn: () => listCredentials("engine_provider"),
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
      notification.success({ message: "已保存，立即生效" });
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      notification.error({
        message: "保存失败",
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

  // 权威的 provider → 启动时真正用的来源（"db" | "env" | "none"）
  const providerLiveSource: Record<string, ProviderSource> = {
    jina: engineStatus?.providers.jina ?? "none",
    tavily: engineStatus?.providers.tavily ?? "none",
    firecrawl: engineStatus?.providers.firecrawl ?? "none",
  };
  // DB 当前是否有对应 provider 记录（可能已改但未重启）
  const providerHasDB: Record<string, boolean> = {
    jina: false,
    tavily: false,
    firecrawl: false,
  };
  providers.forEach((p) => {
    if (p.provider_name) providerHasDB[p.provider_name] = true;
  });

  // 返回工具卡片显示的来源：按 db > env > none 优先级合并该工具相关 provider
  const sourceFor = (
    tool: string,
  ): { source: "db" | "env" | "none"; pendingRestart: boolean } => {
    const relevant = PROVIDERS_BY_TOOL[tool] ?? [];
    if (relevant.length === 0) {
      // api_request 不依赖 provider key，依 tool 注册状态判断
      return {
        source: registered.has(tool) ? "env" : "none",
        pendingRestart: false,
      };
    }
    const liveSources = relevant.map((p) => providerLiveSource[p]);
    let source: "db" | "env" | "none" = "none";
    if (liveSources.includes("db")) source = "db";
    else if (liveSources.includes("env")) source = "env";

    // 待重启：DB 已有记录但后端尚未用 "db"
    const pendingRestart = relevant.some(
      (p) => providerHasDB[p] && providerLiveSource[p] !== "db",
    );
    return { source, pendingRestart };
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
              value={isOn ? "已启用" : "未启用"}
              valueStyle={{
                color: isOn ? "#52c41a" : "#999",
                fontSize: 18,
              }}
            />
            <Tooltip title="每次调用按 URL host 从藏兵阁 Edict 凭证里查对应 token 动态注入 Authorization；不依赖全局 provider key。配置路径：藏兵阁 → 外部凭证 → Edict 凭证">
              <Tag color="purple" style={{ marginTop: 4 }}>
                凭证: 藏兵阁 (Edict 级)
              </Tag>
            </Tooltip>
          </Card>
        </Col>
      );
    }

    const { source, pendingRestart } = sourceFor(name);
    const srcLabel = source === "db" ? "DB" : source === "env" ? "env" : "—";
    const srcColor =
      source === "db" ? "green" : source === "env" ? "blue" : "default";
    return (
      <Col key={name} xs={12} md={6}>
        <Card size="small">
          <Statistic
            title={name}
            value={isOn ? "已启用" : "未启用"}
            valueStyle={{
              color: isOn ? "#52c41a" : "#999",
              fontSize: 18,
            }}
          />
          <Space size={4} style={{ marginTop: 4 }} wrap>
            <Tag color={srcColor}>Key: {srcLabel}</Tag>
            {pendingRestart && (
              <Tooltip title="藏兵阁里已配置新 provider key，但后端 engine 仍用旧来源，需要重启后台生效">
                <Tag color="orange" icon={<ExclamationCircleOutlined />}>
                  待重启
                </Tag>
              </Tooltip>
            )}
          </Space>
        </Card>
      </Col>
    );
  });

  const columns = [
    {
      title: "时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 160,
      render: (v: string) => formatTime(v),
    },
    {
      title: "工具",
      dataIndex: "tool",
      key: "tool",
      width: 110,
      render: (v: string) => (
        <Tag color={TOOL_COLORS[v] ?? "default"}>{v}</Tag>
      ),
    },
    {
      title: "Host",
      dataIndex: "host",
      key: "host",
      ellipsis: true,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: "方法",
      dataIndex: "method",
      key: "method",
      width: 80,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: "状态",
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
      title: "凭证",
      dataIndex: "credential_name",
      key: "credential_name",
      width: 160,
      render: (v: string | null) => v ?? "—",
    },
  ];

  return (
    <PageContainer title="鸿胪寺">
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Card
          title={
            <Space>
              <GlobalOutlined />
              <span>工具状态</span>
            </Space>
          }
          size="small"
        >
          <Row gutter={[12, 12]}>{toolCards}</Row>
          <Typography.Paragraph
            type="secondary"
            style={{ marginTop: 12, marginBottom: 0, fontSize: 12 }}
          >
            启用情况取决于 profile + env keys + 主密钥（
            <code>TIANSHU_SECRET_MASTER_KEY</code> 控制 api_request；
            <code>TIANSHU_FIRECRAWL_API_KEY</code> 控制 web_extract）。
          </Typography.Paragraph>
        </Card>

        <Card
          title={
            <Space>
              <span>引擎偏好（系统默认）</span>
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
              保存
            </Button>
          }
        >
          <Space direction="vertical" style={{ width: "100%" }} size="middle">
            <Typography.Paragraph
              type="secondary"
              style={{ marginBottom: 0, fontSize: 12 }}
            >
              覆盖 profile 预设的引擎选择；Edict 创建时的 override 优先级最高。留空 = 沿用 profile 默认。改动立即生效，无需重启。
            </Typography.Paragraph>

            <Form.Item
              label="web_fetch 引擎链（按顺序尝试，第一个 ok 即返回）"
              style={{ marginBottom: 0 }}
            >
              <Select
                mode="multiple"
                placeholder="留空 = 沿用 profile（如 DEFAULT=local+jina）"
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
              label="fallback_mode（引擎失败/空时是否切下一个）"
              style={{ marginBottom: 0 }}
            >
              <Radio.Group
                value={fallbackMode ?? ""}
                onChange={(e) => setFallbackMode(e.target.value || null)}
              >
                <Radio value="">沿用 profile</Radio>
                <Radio value="on_error_or_empty">失败/空即切</Radio>
                <Radio value="none">只用首个引擎</Radio>
              </Radio.Group>
            </Form.Item>

            <Form.Item label="web_search provider" style={{ marginBottom: 0 }}>
              <Radio.Group
                value={searchProvider ?? ""}
                onChange={(e) => setSearchProvider(e.target.value || null)}
              >
                <Radio value="">沿用 profile</Radio>
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
              <span>凭证管理</span>
            </Space>
          }
          size="small"
          extra={
            <Button
              type="link"
              onClick={() => navigate("/system?tab=external-creds")}
            >
              前往藏兵阁 <RightOutlined />
            </Button>
          }
        >
          <Typography.Paragraph style={{ marginBottom: 0 }}>
            外部 API 凭证（GitHub / Notion / ...）由
            <strong> 藏兵阁 · 外部凭证 </strong>
            加密托管（Fernet 对称加密，主密钥来自 env）。
            LLM 全程不可见 credential value，只按 host 匹配自动注入 Authorization header。
          </Typography.Paragraph>
        </Card>

        <Card
          title="最近访问"
          size="small"
          extra={
            <Button type="link" onClick={() => navigate("/audit?tab=network")}>
              查看全部 <RightOutlined />
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
                  description="还没有网络工具调用记录"
                />
              ),
            }}
          />
        </Card>
      </Space>
    </PageContainer>
  );
}
