import { useEffect, useState } from "react";
import {
  Table,
  Button,
  Tag,
  Popconfirm,
  message,
  Card,
  Select,
  Space,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { ReloadOutlined } from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
import { fetchSessionRules, revokeSessionRule } from "../api/policy";
import type { SessionRule } from "../api/policy";

const sourceColor: Record<string, string> = {
  approval: "blue",
  profile: "geekblue",
  manual: "default",
};

export default function SessionRulesPage() {
  const [rules, setRules] = useState<SessionRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [scope, setScope] = useState<"edict" | "always">("always");

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchSessionRules(scope);
      setRules(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  const filtered =
    sourceFilter === "all"
      ? rules
      : rules.filter((r) => r.source === sourceFilter);

  const handleRevoke = async (ruleId: string) => {
    try {
      await revokeSessionRule(ruleId);
      message.success("规则已撤销");
      await load();
    } catch {
      message.error("撤销失败");
    }
  };

  const columns: ColumnsType<SessionRule> = [
    { title: "Tool", dataIndex: "tool_name", width: 160 },
    { title: "Fingerprint", dataIndex: "arg_fingerprint", ellipsis: true },
    {
      title: "Scope",
      dataIndex: "scope",
      width: 100,
      render: (v: string) => (
        <Tag color={v === "always" ? "purple" : "cyan"}>{v}</Tag>
      ),
    },
    {
      title: "Source",
      dataIndex: "source",
      width: 110,
      render: (v: string) => (
        <Tag color={sourceColor[v] ?? "default"}>{v}</Tag>
      ),
    },
    {
      title: "Granted at",
      dataIndex: "granted_at",
      width: 180,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: "Expires",
      dataIndex: "expires_at",
      width: 140,
      render: (v: string | null) =>
        v ? new Date(v).toLocaleDateString() : "never",
    },
    { title: "Reason", dataIndex: "reason", ellipsis: true },
    {
      title: "Actions",
      width: 110,
      render: (_: unknown, row: SessionRule) => (
        <Popconfirm
          title="撤销此规则?"
          onConfirm={() => handleRevoke(row.rule_id)}
        >
          <Button size="small" danger>
            Revoke
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <PageContainer
      title="Session Rules"
      extra={
        <Button icon={<ReloadOutlined />} onClick={load}>
          刷新
        </Button>
      }
    >
      <Card size="small">
        <Space style={{ marginBottom: 16 }} wrap>
          <Select
            value={scope}
            onChange={(v: "edict" | "always") => setScope(v)}
            style={{ width: 160 }}
            options={[
              { value: "always", label: "Scope: always" },
              { value: "edict", label: "Scope: edict" },
            ]}
          />
          <Select
            value={sourceFilter}
            onChange={setSourceFilter}
            style={{ width: 200 }}
            options={[
              { value: "all", label: "All sources" },
              { value: "approval", label: "Approval" },
              { value: "profile", label: "Profile" },
              { value: "manual", label: "Manual" },
            ]}
          />
        </Space>
        <Table<SessionRule>
          columns={columns}
          dataSource={filtered}
          rowKey="rule_id"
          loading={loading}
          pagination={{ pageSize: 20 }}
          locale={{ emptyText: "暂无规则" }}
          size="small"
        />
      </Card>
    </PageContainer>
  );
}
