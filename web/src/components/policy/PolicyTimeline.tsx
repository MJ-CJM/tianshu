import { useEffect, useMemo, useState } from "react";
import {
  Collapse,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  Empty,
  Select,
  Space,
  Spin,
} from "antd";
import { fetchPolicyEvents } from "../../api/policy";
import type { PolicyEvent } from "../../api/policy";
import { useT } from "../../i18n";

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

type FilterKey = "all" | "policy" | "approval" | "decree" | "hook";

function matchFilter(eventType: string, filter: FilterKey): boolean {
  if (filter === "all") return true;
  if (filter === "policy") return eventType.startsWith("policy.");
  if (filter === "approval") return eventType === "tool.approval_required";
  if (filter === "decree") return eventType.startsWith("decree.");
  if (filter === "hook") return eventType.startsWith("hook.");
  return true;
}

interface Props {
  edictId: string;
  refreshKey?: number;
  defaultOpen?: boolean;
}

export function PolicyTimeline({
  edictId,
  refreshKey,
  defaultOpen = true,
}: Props) {
  const t = useT();
  const filterOptions: { value: FilterKey; label: string }[] = [
    { value: "all", label: t("comp.policyTimeline.filterAll") },
    { value: "policy", label: t("comp.policyTimeline.filterPolicy") },
    { value: "approval", label: t("comp.policyTimeline.filterApproval") },
    { value: "decree", label: t("comp.policyTimeline.filterDecree") },
    { value: "hook", label: t("comp.policyTimeline.filterHook") },
  ];
  const [events, setEvents] = useState<PolicyEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [activeKey, setActiveKey] = useState<string[]>(
    defaultOpen ? ["policy"] : [],
  );

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

  const filteredEvents = useMemo(
    () => events.filter((e) => matchFilter(e.type, filter)),
    [events, filter],
  );

  const counts = useMemo(() => {
    const c = { policy: 0, approval: 0, decree: 0, hook: 0 };
    for (const e of events) {
      if (e.type.startsWith("policy.")) c.policy += 1;
      else if (e.type === "tool.approval_required") c.approval += 1;
      else if (e.type.startsWith("decree.")) c.decree += 1;
      else if (e.type.startsWith("hook.")) c.hook += 1;
    }
    return c;
  }, [events]);

  // 把过滤下拉放到 Collapse 的 extra 里，避免触发折叠展开
  const filterSelect = (
    <Space size={8} onClick={(e) => e.stopPropagation()}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        p:{counts.policy} · a:{counts.approval} · d:{counts.decree} · h:
        {counts.hook}
      </Text>
      <Select
        aria-label={`${t("comp.policyTimeline.title")} ${t("comp.policyTimeline.filterAll")}`}
        size="small"
        value={filter}
        onChange={(v) => setFilter(v as FilterKey)}
        options={filterOptions}
        style={{ width: 220 }}
      />
    </Space>
  );

  const items = filteredEvents.map((e) => {
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

  const body = loading ? (
    <div style={{ padding: 24, textAlign: "center" }}>
      <Spin />
    </div>
  ) : filteredEvents.length === 0 ? (
    <Empty
      description={events.length === 0 ? t("comp.policyTimeline.emptyAll") : t("comp.policyTimeline.emptyFiltered")}
    />
  ) : (
    <Timeline mode="left" items={items} />
  );

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
        {filterSelect}
      </div>
      <Collapse
        activeKey={activeKey}
        onChange={(k) => setActiveKey(Array.isArray(k) ? k : [k])}
        items={[
          {
            key: "policy",
            label: (
              <Space>
                <span>{t("comp.policyTimeline.title")}</span>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  ({events.length})
                </Text>
              </Space>
            ),
            children: body,
          },
        ]}
      />
    </div>
  );
}
