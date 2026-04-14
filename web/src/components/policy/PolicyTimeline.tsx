import { useEffect, useState } from "react";
import { Card, Tag, Timeline, Tooltip, Typography, Empty } from "antd";
import { fetchPolicyEvents } from "../../api/policy";
import type { PolicyEvent } from "../../api/policy";

const { Text } = Typography;

function verdictColor(verdict: string): string {
  switch (verdict) {
    case "allow":
      return "green";
    case "deny":
      return "red";
    case "require_approval":
      return "orange";
    default:
      return "blue";
  }
}

interface Props {
  edictId: string;
  refreshKey?: number;
}

export function PolicyTimeline({ edictId, refreshKey }: Props) {
  const [events, setEvents] = useState<PolicyEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchPolicyEvents(edictId)
      .then((data) => {
        if (!cancelled) setEvents(data);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [edictId, refreshKey]);

  if (!loading && events.length === 0) {
    return (
      <Card title="Policy Timeline" size="small">
        <Empty description="暂无策略事件" />
      </Card>
    );
  }

  const items = events.map((e) => {
    const p = (e.payload ?? {}) as Record<string, unknown>;
    const verdict = (p.verdict as string) ?? "";
    const ruleId = (p.rule_id as string) ?? "";
    const toolName = (p.tool_name as string) ?? "";
    const reason = (p.reason as string) ?? "";
    const label = new Date(e.created_at).toLocaleTimeString();
    const color = verdict ? verdictColor(verdict) : "blue";
    const tag =
      e.type === "policy.decision" ? (
        <Tag color={verdictColor(verdict)}>{verdict}</Tag>
      ) : (
        <Tag>{e.type.replace(/^(policy|hook|tool|decree)\./, "")}</Tag>
      );
    return {
      key: String(e.id),
      color,
      label,
      children: (
        <span>
          {tag}
          {toolName && (
            <Text code style={{ marginLeft: 8 }}>
              {toolName}
            </Text>
          )}
          {ruleId && (
            <Text type="secondary" style={{ marginLeft: 8 }}>
              {ruleId}
            </Text>
          )}
          {reason && (
            <Tooltip title={reason}>
              <Text
                type="secondary"
                ellipsis
                style={{
                  marginLeft: 8,
                  maxWidth: 400,
                  display: "inline-block",
                  verticalAlign: "middle",
                }}
              >
                {reason}
              </Text>
            </Tooltip>
          )}
        </span>
      ),
    };
  });

  return (
    <Card
      title="Policy Timeline"
      size="small"
      loading={loading}
      style={{ marginBottom: 24 }}
    >
      <Timeline mode="left" items={items} />
    </Card>
  );
}
