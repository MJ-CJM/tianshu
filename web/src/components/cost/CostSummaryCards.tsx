import { Card, Col, Row, Statistic } from "antd";
import {
  ThunderboltOutlined,
  FileTextOutlined,
  RiseOutlined,
} from "@ant-design/icons";
import type { CostSummary } from "../../api/types";

interface Props {
  summary: CostSummary | undefined;
  loading: boolean;
}

export default function CostSummaryCards({ summary, loading }: Props) {
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} sm={12} lg={6}>
        <Card>
          <Statistic
            title="总成本 (CNY)"
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
            title="总 Token"
            value={summary?.total_tokens ?? 0}
            prefix={<ThunderboltOutlined />}
            loading={loading}
          />
        </Card>
      </Col>
      <Col xs={24} sm={12} lg={6}>
        <Card>
          <Statistic
            title="输入 Token"
            value={summary?.total_prompt_tokens ?? 0}
            prefix={<FileTextOutlined />}
            loading={loading}
          />
        </Card>
      </Col>
      <Col xs={24} sm={12} lg={6}>
        <Card>
          <Statistic
            title="输出 Token"
            value={summary?.total_completion_tokens ?? 0}
            prefix={<RiseOutlined />}
            loading={loading}
          />
        </Card>
      </Col>
    </Row>
  );
}
