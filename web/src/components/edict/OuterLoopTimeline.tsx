/** Outer loop iterations 可视化 —— 仅当 edict.acceptance != null 时使用。 */

import { useEffect, useState, useMemo, useCallback } from "react";
import { Card, Tag, Collapse, Typography, Space, Empty, Spin } from "antd";
import {
  ClockCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
} from "@ant-design/icons";
import { getOuterLoopIterations } from "../../api/edicts";
import { toApiProblem } from "../../api/client";
import { useWebSocket } from "../../hooks/useWebSocket";
import type { OuterLoopIteration } from "../../api/types";
import type { ApiProblem } from "../../contracts/api";
import { useT } from "../../i18n";
import PageDataState from "../states/PageDataState";
import { problemPageStatus } from "../states/problemPageStatus";

interface ParsedChecksResult {
  all_passed: boolean;
  outcomes: Array<{
    name: string;
    passed: boolean;
    detail?: string | null;
    score?: number | null;
    duration_ms?: number;
  }>;
}

interface ParsedCriticResult {
  verdict: "pass" | "fail";
  issue_class?: string | null;
  feedback?: string;
  suggested_fix?: string | null;
}

const LEVEL_COLORS: Record<string, string> = {
  L0: "default",
  L1: "blue",
  L2: "purple",
  L3: "red",
};

function safeParse<T>(json: string | null): T | null {
  if (!json) return null;
  try {
    return JSON.parse(json) as T;
  } catch {
    return null;
  }
}

function formatDuration(start: string, end: string): string {
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`;
}

interface Props {
  edictId: string;
}

export default function OuterLoopTimeline({ edictId }: Props) {
  const t = useT();
  const [rows, setRows] = useState<OuterLoopIteration[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const { lastMessage } = useWebSocket();

  const fetchRows = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setProblem(null);
    getOuterLoopIterations(edictId)
      .then((res) => {
        if (cancelled) return;
        setRows(res.data ?? []);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setRows(null);
        setProblem(toApiProblem(error));
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [edictId]);

  useEffect(() => {
    setLoading(true);
    return fetchRows();
  }, [fetchRows]);

  // 实时刷新：收到 outer_loop.* 事件且 edict_id 匹配则重 fetch
  useEffect(() => {
    if (!lastMessage) return;
    const msg = lastMessage as { type?: string; edict_id?: string };
    if (
      msg.edict_id === edictId &&
      typeof msg.type === "string" &&
      msg.type.startsWith("outer_loop.")
    ) {
      fetchRows();
    }
  }, [lastMessage, edictId, fetchRows]);

  const totalCost = useMemo(
    () => (rows ?? []).reduce((sum, r) => sum + (r.cost_cny || 0), 0),
    [rows],
  );

  if (loading) {
    return (
      <Card
        size="small"
        title={t("comp.outerLoop.title")}
        style={{ marginTop: 16 }}
      >
        <Spin />
      </Card>
    );
  }

  if (problem) {
    return (
      <Card
        size="small"
        title={t("comp.outerLoop.title")}
        style={{ marginTop: 16 }}
      >
        <PageDataState
          status={problemPageStatus(problem)}
          data={null}
          problem={problem}
          isEmpty={(items: OuterLoopIteration[]) => items.length === 0}
          onRetry={fetchRows}
        >
          {() => null}
        </PageDataState>
      </Card>
    );
  }

  if (!rows || rows.length === 0) {
    return null;
  }

  return (
    <Card
      size="small"
      title={
        <Space>
          <span>{t("comp.outerLoop.title")}</span>
          <Tag color="default">
            {t("comp.outerLoop.rounds", { n: rows.length })}
          </Tag>
          <Tag color="green">
            {t("comp.outerLoop.totalCost", { cost: totalCost.toFixed(4) })}
          </Tag>
        </Space>
      }
      style={{ marginTop: 16 }}
    >
      <Collapse
        ghost
        items={rows.map((r) => {
          const checks = safeParse<ParsedChecksResult>(r.checks_result);
          const critic = safeParse<ParsedCriticResult>(r.critic_result);
          const verdict = critic?.verdict;
          const verdictIcon =
            verdict === "pass" ? (
              <CheckCircleOutlined
                style={{ color: "var(--ts-color-success)" }}
              />
            ) : verdict === "fail" ? (
              <CloseCircleOutlined style={{ color: "var(--ts-color-error)" }} />
            ) : (
              <ClockCircleOutlined />
            );
          const issueClass = critic?.issue_class;
          return {
            key: r.id,
            label: (
              <Space size="small" wrap>
                <span style={{ fontFamily: "monospace" }}>#{r.iteration}</span>
                <Tag color={LEVEL_COLORS[r.level] ?? "default"}>{r.level}</Tag>
                {verdictIcon}
                {verdict && (
                  <span style={{ fontSize: 12 }}>
                    critic:{" "}
                    {verdict === "pass"
                      ? t("comp.outerLoop.criticPass")
                      : t("comp.outerLoop.criticFail")}
                    {issueClass ? ` (${issueClass})` : ""}
                  </span>
                )}
                {checks && !checks.all_passed && (
                  <Tag color="red">{t("comp.outerLoop.checksFailed")}</Tag>
                )}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {formatDuration(r.started_at, r.finished_at)}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  ¥{r.cost_cny.toFixed(4)}
                </Typography.Text>
                {r.archived_at && <Tag>{t("comp.outerLoop.archived")}</Tag>}
              </Space>
            ),
            children: (
              <Space
                direction="vertical"
                style={{ width: "100%" }}
                size="small"
              >
                {checks && (
                  <div>
                    <Typography.Text strong style={{ fontSize: 12 }}>
                      Checks ({checks.outcomes.length})
                    </Typography.Text>
                    <div style={{ marginTop: 4 }}>
                      {checks.outcomes.map((o, idx) => (
                        <Tag key={idx} color={o.passed ? "green" : "red"}>
                          {o.name}
                          {o.score !== undefined && o.score !== null
                            ? ` (${o.score.toFixed(2)})`
                            : ""}
                        </Tag>
                      ))}
                    </div>
                    {checks.outcomes
                      .filter((o) => !o.passed && o.detail)
                      .map((o, idx) => (
                        <Typography.Paragraph
                          key={idx}
                          type="secondary"
                          style={{ fontSize: 12, marginTop: 4 }}
                          ellipsis={{
                            rows: 3,
                            expandable: true,
                            symbol: t("comp.outerLoop.expand"),
                          }}
                        >
                          {o.name}: {o.detail}
                        </Typography.Paragraph>
                      ))}
                  </div>
                )}
                {critic && (
                  <div>
                    <Typography.Text strong style={{ fontSize: 12 }}>
                      Critic
                    </Typography.Text>
                    <Typography.Paragraph
                      style={{
                        fontSize: 12,
                        marginTop: 4,
                        whiteSpace: "pre-wrap",
                      }}
                      ellipsis={{
                        rows: 4,
                        expandable: true,
                        symbol: t("comp.outerLoop.expand"),
                      }}
                    >
                      {critic.feedback || t("comp.outerLoop.noFeedback")}
                    </Typography.Paragraph>
                    {critic.suggested_fix && (
                      <Typography.Paragraph
                        type="secondary"
                        style={{ fontSize: 12, whiteSpace: "pre-wrap" }}
                        ellipsis={{
                          rows: 3,
                          expandable: true,
                          symbol: t("comp.outerLoop.expand"),
                        }}
                      >
                        {t("comp.outerLoop.suggestion")}
                        {critic.suggested_fix}
                      </Typography.Paragraph>
                    )}
                  </div>
                )}
                {r.actor_output ? (
                  <div>
                    <Typography.Text strong style={{ fontSize: 12 }}>
                      Actor Output
                    </Typography.Text>
                    <Typography.Paragraph
                      style={{
                        fontSize: 12,
                        marginTop: 4,
                        whiteSpace: "pre-wrap",
                        fontFamily: "monospace",
                      }}
                      ellipsis={{
                        rows: 6,
                        expandable: true,
                        symbol: t("comp.outerLoop.expand"),
                      }}
                    >
                      {r.actor_output}
                    </Typography.Paragraph>
                  </div>
                ) : (
                  r.archived_at && (
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description={t("comp.outerLoop.actorOutputArchived")}
                      style={{ marginBottom: 0 }}
                    />
                  )
                )}
              </Space>
            ),
          };
        })}
      />
    </Card>
  );
}
