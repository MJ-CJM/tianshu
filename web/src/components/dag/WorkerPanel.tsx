import { Card, Progress, Statistic, Space } from 'antd';
import { useT } from "../../i18n";

interface WorkerPanelProps {
  poolStatus?: {
    active_count: number;
    max_concurrency: number;
    completed_count: number;
    failed_count: number;
  };
  laneStatus?: {
    global: { max_concurrency: number; active: number; available: number };
    sessions: Record<string, { max: number; available: number }>;
  };
}

export default function WorkerPanel({ poolStatus, laneStatus }: WorkerPanelProps) {
  const t = useT();
  if (!poolStatus) return null;

  const utilizationPct = poolStatus.max_concurrency > 0
    ? Math.round((poolStatus.active_count / poolStatus.max_concurrency) * 100)
    : 0;

  return (
    <Card size="small" title="Worker Pool" style={{ width: 240 }}>
      <Progress
        percent={utilizationPct}
        size="small"
        status={utilizationPct >= 90 ? 'exception' : 'active'}
        format={() => `${poolStatus.active_count}/${poolStatus.max_concurrency}`}
      />
      <Space direction="vertical" size={4} style={{ marginTop: 8, width: '100%' }}>
        <Statistic
          title={t("comp.worker.completed")}
          value={poolStatus.completed_count}
          valueStyle={{ fontSize: 16, color: '#52c41a' }}
        />
        <Statistic
          title={t("comp.worker.failed")}
          value={poolStatus.failed_count}
          valueStyle={{ fontSize: 16, color: '#ff4d4f' }}
        />
      </Space>
      {laneStatus?.global && (
        <div style={{ marginTop: 8, borderTop: '1px solid #f0f0f0', paddingTop: 8 }}>
          <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>{t("comp.worker.globalLane")}</div>
          <Progress
            percent={
              laneStatus.global.max_concurrency > 0
                ? Math.round((laneStatus.global.active / laneStatus.global.max_concurrency) * 100)
                : 0
            }
            size="small"
            format={() => `${laneStatus.global.active}/${laneStatus.global.max_concurrency}`}
          />
        </div>
      )}
    </Card>
  );
}
