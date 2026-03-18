import { Statistic, Row, Col } from "antd";
import GlowCard from "../common/GlowCard";
import type { UsageSummary } from "../../api/types";
import { formatTokens } from "../../utils/format";

interface UsageDisplayProps {
  usage: UsageSummary;
}

export default function UsageDisplay({ usage }: UsageDisplayProps) {
  return (
    <GlowCard title="用墨统计" style={{ marginBottom: 24 }}>
      <Row gutter={24}>
        <Col span={8}>
          <Statistic
            title="进言"
            value={formatTokens(usage.prompt_tokens)}
            valueStyle={{ color: "#00d4ff", fontSize: 20 }}
          />
        </Col>
        <Col span={8}>
          <Statistic
            title="批复"
            value={formatTokens(usage.completion_tokens)}
            valueStyle={{ color: "#00d4ff", fontSize: 20 }}
          />
        </Col>
        <Col span={8}>
          <Statistic
            title="合计"
            value={formatTokens(usage.total_tokens)}
            valueStyle={{ color: "#52c41a", fontSize: 20 }}
          />
        </Col>
      </Row>
    </GlowCard>
  );
}
