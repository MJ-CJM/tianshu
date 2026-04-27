/** 监督报告卡片 — 长任务终态后由 critic persona 产出的 4 章节总评。 */

import { useEffect, useState } from "react";
import { Card, Empty, List, Space, Spin, Tag, Typography } from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  BulbOutlined,
} from "@ant-design/icons";
import { getSupervisionReport } from "../../api/edicts";
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
}

export default function SupervisionReportCard({ edictId }: Props) {
  const [report, setReport] = useState<SupervisionReport | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getSupervisionReport(edictId)
      .then((r) => {
        if (cancelled) return;
        setReport(r);
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [edictId]);

  if (loading) {
    return (
      <Card size="small" title="监督报告" style={{ marginTop: 16 }}>
        <Spin />
      </Card>
    );
  }

  // 短任务 / 未启用 critic → 不显示卡片
  if (!report) {
    return null;
  }

  return (
    <Card
      size="small"
      title={
        <Space wrap>
          <span>监督报告</span>
          <Tag color="purple">{report.persona_name}</Tag>
          <Tag color={STATUS_COLORS[report.final_status] ?? "default"}>
            {STATUS_LABELS[report.final_status] ?? report.final_status}
          </Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {report.iterations_count} 轮 · ¥{report.total_cost_cny.toFixed(4)}
          </Typography.Text>
        </Space>
      }
      style={{ marginTop: 16 }}
    >
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        {/* 观察到的问题 */}
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

        {/* 做得好 */}
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

        {/* 做得不够 */}
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

        {/* 建议 */}
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

        {/* 全空兜底（LLM 解析失败） */}
        {report.issues_observed.length === 0 &&
          report.well_done.length === 0 &&
          report.poorly_done.length === 0 &&
          !report.recommendation && (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="监督报告生成失败或解析异常"
            >
              {report.raw_feedback && (
                <Typography.Paragraph
                  type="secondary"
                  style={{ fontSize: 12, whiteSpace: "pre-wrap", textAlign: "left" }}
                  ellipsis={{ rows: 5, expandable: true, symbol: "展开" }}
                >
                  原始输出：{report.raw_feedback}
                </Typography.Paragraph>
              )}
            </Empty>
          )}
      </Space>
    </Card>
  );
}
