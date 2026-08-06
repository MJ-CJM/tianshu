import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Tooltip } from "antd";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import styles from "./DagNode.module.css";

type DAGNodeStatus =
  "pending" | "ready" | "running" | "completed" | "failed" | "cancelled";

interface DagNodeData {
  label: string;
  status: DAGNodeStatus;
  nodeId?: string;
  assignedOfficial?: string;
  officialName?: string;
  officialDept?: string;
  error?: string;
  preview?: boolean;
}

// 语义色走 CSS 变量,注入 .plaque 的 --status-color,浅/深主题都成立
const STATUS_CONFIG: Record<
  DAGNodeStatus,
  { color: string; icon: React.ReactNode }
> = {
  pending: {
    color: "var(--ts-status-cancelled)",
    icon: <ClockCircleOutlined />,
  },
  ready: { color: "var(--ts-status-running)", icon: <SyncOutlined spin /> },
  running: { color: "var(--ts-color-warning)", icon: <LoadingOutlined spin /> },
  completed: {
    color: "var(--ts-status-completed)",
    icon: <CheckCircleOutlined />,
  },
  failed: { color: "var(--ts-status-failed)", icon: <CloseCircleOutlined /> },
  cancelled: {
    color: "var(--ts-status-cancelled)",
    icon: <MinusCircleOutlined />,
  },
};

const HANDLE_STYLE: React.CSSProperties = {
  width: 8,
  height: 8,
  background: "var(--ts-color-bg-container)",
  border: "1.5px solid var(--ts-color-border-hover)",
};

function DagNodeComponent({ data }: NodeProps) {
  const nodeData = data as unknown as DagNodeData;
  const status = nodeData.status || "pending";
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  const officialLabel = nodeData.officialName || nodeData.assignedOfficial;

  const classNames = [
    styles.plaque,
    status === "running" ? styles.running : "",
    status === "cancelled" ? styles.cancelled : "",
    nodeData.preview ? styles.preview : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classNames}
      style={{ "--status-color": config.color } as React.CSSProperties}
    >
      <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
      <div className={styles.header}>
        <span className={styles.statusIcon}>{config.icon}</span>
        <span className={styles.statusText}>{status}</span>
        {nodeData.nodeId && (
          <span className={styles.nodeId}>{nodeData.nodeId}</span>
        )}
      </div>
      <Tooltip title={nodeData.label}>
        <div className={styles.desc}>{nodeData.label}</div>
      </Tooltip>
      <div className={styles.official}>
        {officialLabel ? (
          <>
            <span className={styles.seal}>{officialLabel.charAt(0)}</span>
            <span className={styles.officialName}>{officialLabel}</span>
            {nodeData.officialDept && (
              <span className={styles.officialDept}>
                · {nodeData.officialDept}
              </span>
            )}
          </>
        ) : (
          <>
            <span className={`${styles.seal} ${styles.sealEmpty}`}>?</span>
            <span className={styles.officialDept}>—</span>
          </>
        )}
      </div>
      {nodeData.error && (
        <Tooltip title={nodeData.error}>
          <span className={styles.error}>{nodeData.error}</span>
        </Tooltip>
      )}
      <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
    </div>
  );
}

export default memo(DagNodeComponent);
