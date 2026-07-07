/** 监督报告卡片 — 长任务终态后由 critic persona(s) 产出的 4 章节总评。
 *
 * 多监督官时用 Tabs 切换不同 persona 的报告。
 */

import { useEffect, useState, useCallback } from "react";
import {
  Card,
  Empty,
  List,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  BulbOutlined,
} from "@ant-design/icons";
import { getSupervisionReports } from "../../api/edicts";
import { useWebSocket } from "../../hooks/useWebSocket";
import type { SupervisionReport } from "../../api/types";
import { useT, type TFunction } from "../../i18n";

const STATUS_COLORS: Record<string, string> = {
  completed: "green",
  failed: "red",
  cancelled: "orange",
  running: "blue",
};

interface Props {
  edictId: string;
  /** 按 memorial 过滤；不传则展示该 edict 全部（含历次 follow-up）。 */
  memorialId?: string;
}

function ReportContent({ report, t }: { report: SupervisionReport; t: TFunction }) {
  const allEmpty =
    report.issues_observed.length === 0 &&
    report.well_done.length === 0 &&
    report.poorly_done.length === 0 &&
    !report.recommendation;

  if (allEmpty) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t("comp.supervision.failTitle")}
      >
        {report.raw_feedback && (
          <Typography.Paragraph
            type="secondary"
            style={{
              fontSize: 12,
              whiteSpace: "pre-wrap",
              textAlign: "left",
            }}
            ellipsis={{ rows: 5, expandable: true, symbol: t("comp.supervision.expand") }}
          >
            {t("comp.supervision.rawOutput")}{report.raw_feedback}
          </Typography.Paragraph>
        )}
      </Empty>
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      {report.issues_observed.length > 0 && (
        <div>
          <Typography.Text strong>
            <CloseCircleOutlined style={{ color: "#ff4d4f", marginRight: 6 }} />
            {t("comp.supervision.issues", { n: report.issues_observed.length })}
          </Typography.Text>
          <List
            size="small"
            dataSource={report.issues_observed}
            renderItem={(it) => (
              <List.Item style={{ padding: "4px 0", border: "none" }}>
                • {it}
              </List.Item>
            )}
          />
        </div>
      )}

      {report.well_done.length > 0 && (
        <div>
          <Typography.Text strong>
            <CheckCircleOutlined style={{ color: "#52c41a", marginRight: 6 }} />
            {t("comp.supervision.wellDone", { n: report.well_done.length })}
          </Typography.Text>
          <List
            size="small"
            dataSource={report.well_done}
            renderItem={(it) => (
              <List.Item style={{ padding: "4px 0", border: "none" }}>
                • {it}
              </List.Item>
            )}
          />
        </div>
      )}

      {report.poorly_done.length > 0 && (
        <div>
          <Typography.Text strong>
            <ExclamationCircleOutlined
              style={{ color: "#faad14", marginRight: 6 }}
            />
            {t("comp.supervision.poorlyDone", { n: report.poorly_done.length })}
          </Typography.Text>
          <List
            size="small"
            dataSource={report.poorly_done}
            renderItem={(it) => (
              <List.Item style={{ padding: "4px 0", border: "none" }}>
                • {it}
              </List.Item>
            )}
          />
        </div>
      )}

      {report.recommendation && (
        <div>
          <Typography.Text strong>
            <BulbOutlined style={{ color: "#1890ff", marginRight: 6 }} />
            {t("comp.supervision.recommendation")}
          </Typography.Text>
          <Typography.Paragraph
            style={{ marginTop: 4, whiteSpace: "pre-wrap" }}
          >
            {report.recommendation}
          </Typography.Paragraph>
        </div>
      )}
    </Space>
  );
}

export default function SupervisionReportCard({ edictId, memorialId }: Props) {
  const t = useT();
  const [reports, setReports] = useState<SupervisionReport[]>([]);
  const [loading, setLoading] = useState(true);
  const { lastMessage } = useWebSocket();

  const fetchReports = useCallback(() => {
    let cancelled = false;
    getSupervisionReports(edictId)
      .then((rs) => {
        if (cancelled) return;
        const filtered = memorialId
          ? rs.filter((r) => r.memorial_id === memorialId)
          : rs;
        setReports(filtered);
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [edictId, memorialId]);

  useEffect(() => {
    setLoading(true);
    return fetchReports();
  }, [fetchReports]);

  // 实时刷新：监督报告生成事件触发重 fetch
  useEffect(() => {
    if (!lastMessage) return;
    const msg = lastMessage as { type?: string; edict_id?: string };
    if (
      msg.edict_id === edictId &&
      msg.type === "outer_loop.supervision_completed"
    ) {
      fetchReports();
    }
  }, [lastMessage, edictId, fetchReports]);

  if (loading) {
    return (
      <Card size="small" title={t("comp.supervision.title")} style={{ marginTop: 16 }}>
        <Spin />
      </Card>
    );
  }

  if (reports.length === 0) {
    return null;
  }

  // Single supervisor: expand directly
  if (reports.length === 1) {
    const r = reports[0]!;
    return (
      <Card
        size="small"
        title={
          <Space wrap>
            <span>{t("comp.supervision.title")}</span>
            <Tag color="purple">{r.persona_name}</Tag>
            <Tag color={STATUS_COLORS[r.final_status] ?? "default"}>
              {t(`comp.supervision.status.${r.final_status}`)}
            </Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("comp.supervision.iterCost", { n: r.iterations_count, cost: r.total_cost_cny.toFixed(4) })}
            </Typography.Text>
          </Space>
        }
        style={{ marginTop: 16 }}
      >
        <ReportContent report={r} t={t} />
      </Card>
    );
  }

  // Multiple supervisors: use Tabs
  const first = reports[0]!;
  const totalCost = reports.reduce((s, r) => s + r.total_cost_cny, 0);
  return (
    <Card
      size="small"
      title={
        <Space wrap>
          <span>{t("comp.supervision.title")}</span>
          <Tag color="purple">{t("comp.supervision.supervisorCount", { n: reports.length })}</Tag>
          <Tag color={STATUS_COLORS[first.final_status] ?? "default"}>
            {t(`comp.supervision.status.${first.final_status}`)}
          </Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("comp.supervision.iterTotalCost", { n: first.iterations_count, cost: totalCost.toFixed(4) })}
          </Typography.Text>
        </Space>
      }
      style={{ marginTop: 16 }}
    >
      <Tabs
        items={reports.map((r) => ({
          key: r.persona_id,
          label: r.persona_name,
          children: <ReportContent report={r} t={t} />,
        }))}
      />
    </Card>
  );
}
