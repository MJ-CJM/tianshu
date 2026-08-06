import { Button, Space, Popconfirm, Tag } from "antd";
import { StopOutlined, ReloadOutlined } from "@ant-design/icons";
import { useT } from "../../i18n";

interface DagToolbarProps {
  dagId: string;
  status: string;
  onCancel?: () => void;
  onRetry?: () => void;
  cancelLoading?: boolean;
  retryLoading?: boolean;
}

const STATUS_COLORS: Record<string, string> = {
  pending: "default",
  running: "processing",
  completed: "success",
  failed: "error",
  cancelled: "default",
};

export default function DagToolbar({
  dagId,
  status,
  onCancel,
  onRetry,
  cancelLoading,
  retryLoading,
}: DagToolbarProps) {
  const t = useT();
  const canCancel =
    !!onCancel && (status === "running" || status === "pending");
  const canRetry = !!onRetry && (status === "failed" || status === "cancelled");

  return (
    <div
      style={{
        padding: "8px 16px",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        borderBottom: "1px solid var(--ts-color-border)",
        background: "var(--ts-color-bg-container)",
      }}
    >
      <Space>
        <Tag color={STATUS_COLORS[status] || "default"}>
          {status.toUpperCase()}
        </Tag>
        <span style={{ fontSize: 12, color: "var(--ts-color-text-secondary)" }}>
          {dagId}
        </span>
      </Space>
      <Space>
        {canCancel && (
          <Popconfirm title={t("comp.dag.cancelConfirm")} onConfirm={onCancel}>
            <Button
              icon={<StopOutlined />}
              danger
              loading={cancelLoading}
              size="small"
            >
              {t("action.cancel")}
            </Button>
          </Popconfirm>
        )}
        {canRetry && (
          <Popconfirm title={t("comp.dag.retryConfirm")} onConfirm={onRetry}>
            <Button
              icon={<ReloadOutlined />}
              loading={retryLoading}
              size="small"
            >
              {t("action.retry")}
            </Button>
          </Popconfirm>
        )}
      </Space>
    </div>
  );
}
