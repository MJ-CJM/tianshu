import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Tooltip, Typography } from 'antd';
import SemanticTag from '../common/SemanticTag';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  SyncOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

type DAGNodeStatus = 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'cancelled';

interface DagNodeData {
  label: string;
  status: DAGNodeStatus;
  assignedOfficial?: string;
  error?: string;
}

// 语义色走 CSS 变量,底色用 color-mix 淡染,浅/深主题都成立
const STATUS_CONFIG: Record<DAGNodeStatus, { color: string; icon: React.ReactNode }> = {
  pending: { color: 'var(--ts-status-cancelled)', icon: <ClockCircleOutlined /> },
  ready: { color: 'var(--ts-status-running)', icon: <SyncOutlined spin /> },
  running: { color: 'var(--ts-color-warning)', icon: <LoadingOutlined spin /> },
  completed: { color: 'var(--ts-status-completed)', icon: <CheckCircleOutlined /> },
  failed: { color: 'var(--ts-status-failed)', icon: <CloseCircleOutlined /> },
  cancelled: { color: 'var(--ts-status-cancelled)', icon: <MinusCircleOutlined /> },
};

function DagNodeComponent({ data }: NodeProps) {
  const nodeData = data as unknown as DagNodeData;
  const config = STATUS_CONFIG[nodeData.status] || STATUS_CONFIG.pending;

  return (
    <div
      style={{
        padding: '8px 12px',
        borderRadius: 8,
        border: `2px solid color-mix(in srgb, ${config.color} 55%, transparent)`,
        background: `color-mix(in srgb, ${config.color} 9%, var(--ts-color-bg-container))`,
        minWidth: 180,
        maxWidth: 260,
      }}
    >
      <Handle type="target" position={Position.Top} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ color: config.color, fontSize: 16 }}>{config.icon}</span>
        <SemanticTag colorVar={config.color} style={{ margin: 0, fontSize: 11 }}>
          {nodeData.status}
        </SemanticTag>
      </div>
      <Tooltip title={nodeData.label}>
        <Text
          style={{
            fontSize: 13,
            display: 'block',
            textDecoration: nodeData.status === 'cancelled' ? 'line-through' : undefined,
          }}
          ellipsis
        >
          {nodeData.label}
        </Text>
      </Tooltip>
      {nodeData.assignedOfficial && (
        <Text type="secondary" style={{ fontSize: 11 }}>
          {nodeData.assignedOfficial}
        </Text>
      )}
      {nodeData.error && (
        <Tooltip title={nodeData.error}>
          <Text type="danger" style={{ fontSize: 11, display: 'block' }} ellipsis>
            {nodeData.error}
          </Text>
        </Tooltip>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

export default memo(DagNodeComponent);
