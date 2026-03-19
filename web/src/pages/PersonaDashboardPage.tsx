import { useState } from "react";
import { Row, Col, Tag, Space, Typography, Statistic, Progress, Spin, Empty, theme } from "antd";
import { CheckCircleOutlined } from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import { usePersonas, usePersonaMetrics } from "../hooks/usePersonas";
import type { PersonaInfo } from "../api/types";

function PersonaCard({
  persona,
  expanded,
  onToggle,
}: {
  persona: PersonaInfo;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { token } = theme.useToken();
  const { data: metrics, isLoading } = usePersonaMetrics(expanded ? persona.id : null);

  return (
    <GlowCard
      hoverable
      style={{ cursor: "pointer", height: "100%" }}
      title={
        <Space>
          <span>{persona.name}</span>
          <Tag color="blue">{persona.department}</Tag>
          {persona.can_delegate && (
            <Tag icon={<CheckCircleOutlined />} color="green">
              可委派
            </Tag>
          )}
        </Space>
      }
      onClick={onToggle}
    >
      <div style={{ marginBottom: 8 }}>
        <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
          可用工具
        </Typography.Text>
        <div style={{ marginTop: 4 }}>
          {persona.tools_allowed.length > 0 ? (
            persona.tools_allowed.map((tool) => (
              <Tag key={tool} style={{ marginBottom: 4, fontSize: 11 }}>
                {tool}
              </Tag>
            ))
          ) : (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              无特定工具
            </Typography.Text>
          )}
        </div>
      </div>

      {expanded && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 12,
            borderTop: `1px solid ${token.colorBorder}`,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {isLoading ? (
            <div style={{ textAlign: "center", padding: 16 }}>
              <Spin size="small" />
            </div>
          ) : metrics ? (
            <Space direction="vertical" size="small" style={{ width: "100%" }}>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title="总执行"
                    value={metrics.total_executions}
                    valueStyle={{ fontSize: 18 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="完成"
                    value={metrics.completed}
                    valueStyle={{ fontSize: 18, color: token.colorSuccess }}
                  />
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title="失败"
                    value={metrics.failed}
                    valueStyle={{ fontSize: 18, color: token.colorError }}
                  />
                </Col>
                <Col span={12}>
                  <div>
                    <Typography.Text
                      style={{ fontSize: 12, color: token.colorTextSecondary }}
                    >
                      成功率
                    </Typography.Text>
                    <Progress
                      percent={Number((metrics.success_rate * 100).toFixed(1))}
                      size="small"
                      status={metrics.success_rate >= 0.8 ? "success" : "normal"}
                    />
                  </div>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title="总 Token"
                    value={metrics.total_tokens}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="均 Token"
                    value={metrics.avg_tokens_per_execution}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title="总成本"
                    value={metrics.total_cost_cny}
                    prefix="¥"
                    precision={4}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="均耗时"
                    value={metrics.avg_duration_seconds}
                    suffix="s"
                    precision={1}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
              </Row>
            </Space>
          ) : (
            <Typography.Text type="secondary">暂无指标数据</Typography.Text>
          )}
        </div>
      )}
    </GlowCard>
  );
}

export default function PersonaDashboardPage() {
  const { data: personas, isLoading } = usePersonas();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <PageContainer title="百官阁">
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }

  if (!personas || personas.length === 0) {
    return (
      <PageContainer title="百官阁">
        <Empty description="暂无百官配置" />
      </PageContainer>
    );
  }

  return (
    <PageContainer title="百官阁">
      <Row gutter={[16, 16]}>
        {personas.map((persona) => (
          <Col key={persona.id} xs={24} sm={12} lg={8}>
            <PersonaCard
              persona={persona}
              expanded={expandedId === persona.id}
              onToggle={() =>
                setExpandedId((prev) => (prev === persona.id ? null : persona.id))
              }
            />
          </Col>
        ))}
      </Row>
    </PageContainer>
  );
}
