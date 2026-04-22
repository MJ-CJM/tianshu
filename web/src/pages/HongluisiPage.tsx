import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
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
} from "antd";
import {
  GlobalOutlined,
  KeyOutlined,
  RightOutlined,
} from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
import { useTools } from "../hooks/useSystem";
import { listNetworkEvents } from "../api/network_events";
import { listCredentials } from "../api/credentials";
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
  const { data: providers = [] } = useQuery({
    queryKey: ["credentials", "engine_provider"],
    queryFn: () => listCredentials("engine_provider"),
    staleTime: 30000,
  });
  const [recent, setRecent] = useState<NetworkEventRow[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    listNetworkEvents({ limit: 20 })
      .then(setRecent)
      .catch(() => setRecent([]))
      .finally(() => setLoading(false));
  }, []);

  const registered = new Set(tools.map((t: { name: string }) => t.name));

  // provider_name → "db"（DB 有配置）
  const providerHasDB: Record<string, boolean> = {
    jina: false,
    tavily: false,
    firecrawl: false,
  };
  providers.forEach((p) => {
    if (p.provider_name) providerHasDB[p.provider_name] = true;
  });

  const sourceFor = (tool: string): "db" | "env" | "none" => {
    const relevant = PROVIDERS_BY_TOOL[tool] ?? [];
    if (relevant.some((p) => providerHasDB[p])) return "db";
    if (registered.has(tool)) return "env";
    return "none";
  };

  const toolCards = NETWORK_TOOL_NAMES.map((name) => {
    const isOn = registered.has(name);
    const src = sourceFor(name);
    const srcLabel = src === "db" ? "DB" : src === "env" ? "env" : "—";
    const srcColor =
      src === "db" ? "green" : src === "env" ? "blue" : "default";
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
          <Tag color={srcColor} style={{ marginTop: 4 }}>
            Key: {srcLabel}
          </Tag>
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
