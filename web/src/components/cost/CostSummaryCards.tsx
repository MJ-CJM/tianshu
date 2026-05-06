import { Card, Col, Row, Statistic } from "antd";
import {
  ThunderboltOutlined,
  FileTextOutlined,
  RiseOutlined,
} from "@ant-design/icons";
import type { CostSummary } from "../../api/types";
import { useT } from "../../i18n";

interface Props {
  summary: CostSummary | undefined;
  loading: boolean;
}

export default function CostSummaryCards({ summary, loading }: Props) {
  const t = useT();
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} sm={12} lg={6}>
        <Card>
          <Statistic
            title={t("cost.summary.totalCost")}
            value={summary?.total_cost_cny ?? 0}
            precision={4}
            prefix="¥"
            loading={loading}
          />
        </Card>
      </Col>
      <Col xs={24} sm={12} lg={6}>
        <Card>
          <Statistic
            title={t("cost.summary.totalTokens")}
            value={summary?.total_tokens ?? 0}
            prefix={<ThunderboltOutlined />}
            loading={loading}
          />
        </Card>
      </Col>
      <Col xs={24} sm={12} lg={6}>
        <Card>
          <Statistic
            title={t("cost.summary.promptTokens")}
            value={summary?.total_prompt_tokens ?? 0}
            prefix={<FileTextOutlined />}
            loading={loading}
          />
        </Card>
      </Col>
      <Col xs={24} sm={12} lg={6}>
        <Card>
          <Statistic
            title={t("cost.summary.completionTokens")}
            value={summary?.total_completion_tokens ?? 0}
            prefix={<RiseOutlined />}
            loading={loading}
          />
        </Card>
      </Col>
    </Row>
  );
}
