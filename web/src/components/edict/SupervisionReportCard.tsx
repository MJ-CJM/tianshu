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

const STATUS_COLORS: Record<string, string> = {
  completed: "green",
  failed: "red",
  cancelled: "orange",
  running: "blue",
};

const STATUS_LABELS: Record<string, string> = {
  completed: "完成",
  failed: "失败",
  cancelled: "取消",
  running: "进行中",
  submitted: "已提交",
};

interface Props {
  edictId: string;
  /** 按 memorial 过滤；不传则展示该 edict 全部（含历次 follow-up）。 */
  memorialId?: string;
}

function ReportContent({ report }: { report: SupervisionReport }) {
  const allEmpty =
    report.issues_observed.length === 0 &&
    report.well_done.length === 0 &&
    report.poorly_done.length === 0 &&
    !report.recommendation;

  if (allEmpty) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="监督报告生成失败或解析异常"
      >
        {report.raw_feedback && (
          <Typography.Paragraph
            type="secondary"
            style={{
              fontSize: 12,
              whiteSpace: "pre-wrap",
              textAlign: "left",
            }}
            ellipsis={{ rows: 5, expandable: true, symbol: "展开" }}
          >
            原始输出：{report.raw_feedback}
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
            观察到的问题（{report.issues_observed.length}）
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
            做得好的地方（{report.well_done.length}）
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
            做得不够的地方（{report.poorly_done.length}）
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
            建议
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
      <Card size="small" title="监督报告" style={{ marginTop: 16 }}>
        <Spin />
      </Card>
    );
  }

  if (reports.length === 0) {
    return null;
  }

  // 单监督官：直接展开
  if (reports.length === 1) {
    const r = reports[0]!;
    return (
      <Card
        size="small"
        title={
          <Space wrap>
            <span>监督报告</span>
            <Tag color="purple">{r.persona_name}</Tag>
            <Tag color={STATUS_COLORS[r.final_status] ?? "default"}>
              {STATUS_LABELS[r.final_status] ?? r.final_status}
            </Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {r.iterations_count} 轮 · ¥{r.total_cost_cny.toFixed(4)}
            </Typography.Text>
          </Space>
        }
        style={{ marginTop: 16 }}
      >
        <ReportContent report={r} />
      </Card>
    );
  }

  // 多监督官：用 Tabs 切换
  const first = reports[0]!;
  return (
    <Card
      size="small"
      title={
        <Space wrap>
          <span>监督报告</span>
          <Tag color="purple">{reports.length} 位监督官</Tag>
          <Tag color={STATUS_COLORS[first.final_status] ?? "default"}>
            {STATUS_LABELS[first.final_status] ?? first.final_status}
          </Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {first.iterations_count} 轮 · 总成本 ¥
            {reports
              .reduce((s, r) => s + r.total_cost_cny, 0)
              .toFixed(4)}
          </Typography.Text>
        </Space>
      }
      style={{ marginTop: 16 }}
    >
      <Tabs
        items={reports.map((r) => ({
          key: r.persona_id,
          label: r.persona_name,
          children: <ReportContent report={r} />,
        }))}
      />
    </Card>
  );
}
