import { useParams } from 'react-router-dom';
import { Spin, Result } from 'antd';
import DagBattleMap from '../components/dag/DagBattleMap';
import { useDag, useCancelDag, useRetryDag, useWorkersStatus } from '../hooks/useDag';
import { useT } from "../i18n";

export default function DagBattleMapPage() {
  const t = useT();
  const { dagId } = useParams<{ dagId: string }>();
  const { data: execution, isLoading, error } = useDag(dagId);
  const { data: workersStatus } = useWorkersStatus();
  const cancelMutation = useCancelDag();
  const retryMutation = useRetryDag();

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '80vh' }}>
        <Spin size="large" tip={t("page.edictDetail.viewBattleMap")} />
      </div>
    );
  }

  if (error) {
    return <Result status="error" title={t("comp.profile.loadFailed")} subTitle={String(error)} />;
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
