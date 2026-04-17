import { Button, Space, Popconfirm, Tag } from 'antd';
import {
  StopOutlined,
  ReloadOutlined,
} from '@ant-design/icons';

interface DagToolbarProps {
  dagId: string;
  status: string;
  onCancel: () => void;
  onRetry: () => void;
  cancelLoading?: boolean;
  retryLoading?: boolean;
}

const STATUS_COLORS: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'default',
};

export default function DagToolbar({
  dagId,
  status,
  onCancel,
  onRetry,
  cancelLoading,
  retryLoading,
}: DagToolbarProps) {
  const canCancel = status === 'running' || status === 'pending';
  const canRetry = status === 'failed' || status === 'cancelled';

  return (
    <div
      style={{
        padding: '8px 16px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        borderBottom: '1px solid #f0f0f0',
        background: '#fff',
      }}
    >
      <Space>
        <Tag color={STATUS_COLORS[status] || 'default'}>{status.toUpperCase()}</Tag>
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>{dagId}</span>
      </Space>
      <Space>
        {canCancel && (
          <Popconfirm title="确定取消整个 DAG 执行？" onConfirm={onCancel}>
            <Button icon={<StopOutlined />} danger loading={cancelLoading} size="small">
              取消
            </Button>
          </Popconfirm>
        )}
        {canRetry && (
          <Popconfirm title="重试失败的节点？" onConfirm={onRetry}>
            <Button icon={<ReloadOutlined />} loading={retryLoading} size="small">
              重试
            </Button>
          </Popconfirm>
        )}
      </Space>
    </div>
  );
}
