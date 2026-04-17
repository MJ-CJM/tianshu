import { useParams } from 'react-router-dom';
import { Spin, Result } from 'antd';
import DagBattleMap from '../components/dag/DagBattleMap';
import { useDag, useCancelDag, useRetryDag, useWorkersStatus } from '../hooks/useDag';

export default function DagBattleMapPage() {
  const { dagId } = useParams<{ dagId: string }>();
  const { data: execution, isLoading, error } = useDag(dagId);
  const { data: workersStatus } = useWorkersStatus();
  const cancelMutation = useCancelDag();
  const retryMutation = useRetryDag();

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '80vh' }}>
        <Spin size="large" tip="加载作战图..." />
      </div>
    );
  }

  if (error) {
    return <Result status="error" title="加载失败" subTitle={String(error)} />;
  }

  return (
    <div style={{ height: 'calc(100vh - 120px)' }}>
      <DagBattleMap
        execution={execution}
        poolStatus={workersStatus?.pool}
        laneStatus={workersStatus?.lanes}
        onCancel={() => dagId && cancelMutation.mutate(dagId)}
        onRetry={() => dagId && retryMutation.mutate({ dagId })}
        cancelLoading={cancelMutation.isPending}
        retryLoading={retryMutation.isPending}
      />
    </div>
  );
}
