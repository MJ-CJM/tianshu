import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Tag, Tooltip, Typography } from 'antd';
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

const STATUS_CONFIG: Record<DAGNodeStatus, { color: string; icon: React.ReactNode; bg: string }> = {
  pending: { color: '#8c8c8c', icon: <ClockCircleOutlined />, bg: '#fafafa' },
  ready: { color: '#1890ff', icon: <SyncOutlined spin />, bg: '#e6f7ff' },
  running: { color: '#faad14', icon: <LoadingOutlined spin />, bg: '#fff7e6' },
  completed: { color: '#52c41a', icon: <CheckCircleOutlined />, bg: '#f6ffed' },
  failed: { color: '#ff4d4f', icon: <CloseCircleOutlined />, bg: '#fff2f0' },
  cancelled: { color: '#595959', icon: <MinusCircleOutlined />, bg: '#f5f5f5' },
};

function DagNodeComponent({ data }: NodeProps) {
  const nodeData = data as unknown as DagNodeData;
  const config = STATUS_CONFIG[nodeData.status] || STATUS_CONFIG.pending;

  return (
    <div
      style={{
        padding: '8px 12px',
        borderRadius: 8,
        border: `2px solid ${config.color}`,
        background: config.bg,
        minWidth: 180,
        maxWidth: 260,
      }}
    >
      <Handle type="target" position={Position.Top} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ color: config.color, fontSize: 16 }}>{config.icon}</span>
        <Tag color={config.color} style={{ margin: 0, fontSize: 11 }}>
          {nodeData.status}
        </Tag>
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
