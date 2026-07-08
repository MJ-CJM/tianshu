import { useState } from "react";
import {
  Alert,
  Button,
  Input,
  Popover,
  Radio,
  Space,
  Tag,
  Typography,
  theme,
  App,
} from "antd";
import {
  BulbOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useSubmitToolDecision } from "../../hooks/useApprovals";
import { useT } from "../../i18n";
import type { PendingToolCall, ToolGrantScope } from "../../api/types";

interface Props {
  pending: PendingToolCall;
}

export default function PendingToolCallCard({ pending }: Props) {
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const mutation = useSubmitToolDecision();
  const [scope, setScope] = useState<ToolGrantScope>("once");
  const [comment, setComment] = useState("");
  const [open, setOpen] = useState(false);
  const t = useT();

  const handleApprove = () => {
    mutation.mutate(
      {
        memorial_id: pending.memorial_id,
        action: "approve",
        comment: comment || undefined,
        grant_scope: scope,
        grant_reason: scope !== "once" ? `granted via approval queue (${scope})` : undefined,
        actor: "user",
      },
      {
        onSuccess: () => {
          message.success(
            scope === "once"
              ? t("toast.toolApproved", { tool: pending.tool_name })
              : t("toast.toolApprovedWithRule", { scope }),
          );
          setOpen(false);
          setComment("");
        },
        onError: (err: unknown) => {
          const msg = err instanceof Error ? err.message : t("toast.actionFailed");
          message.error(msg);
        },
      },
    );
  };

  const handleReject = () => {
    mutation.mutate(
      {
        memorial_id: pending.memorial_id,
        action: "reject",
        comment: comment || "rejected by reviewer",
        actor: "user",
      },
      {
        onSuccess: () => {
          message.warning(t("toast.toolRejected", { tool: pending.tool_name }));
          setOpen(false);
          setComment("");
        },
      },
    );
  };

  const handleGuide = () => {
    if (!comment.trim()) {
      message.warning(t("pendingTool.guideNeedsComment"));
      return;
    }
    mutation.mutate(
      { memorial_id: pending.memorial_id, action: "guide", comment, actor: "user" },
      {
        onSuccess: () => {
          message.info(t("toast.toolGuided", { tool: pending.tool_name }));
          setOpen(false);
          setComment("");
        },
        onError: (err: unknown) => {
          message.error(err instanceof Error ? err.message : t("toast.actionFailed"));
        },
      },
    );
  };

  const popover = (
    <div style={{ minWidth: 260 }}>
      <div style={{ marginBottom: 8 }}>
        <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
          {t("pendingTool.grantScope")}
        </Typography.Text>
      </div>
      <Radio.Group
        value={scope}
        onChange={(e) => setScope(e.target.value)}
        optionType="button"
        buttonStyle="solid"
        size="small"
        style={{ marginBottom: 12 }}
      >
        <Radio.Button value="once">{t("pendingTool.scope.once")}</Radio.Button>
        <Radio.Button value="edict">{t("pendingTool.scope.edict")}</Radio.Button>
        <Radio.Button value="always">{t("pendingTool.scope.always")}</Radio.Button>
      </Radio.Group>
      <Input.TextArea
        rows={2}
        placeholder={t("pendingTool.comment")}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        style={{ marginBottom: 8 }}
      />
      <Space>
        <Button
          type="primary"
          size="small"
          icon={<CheckCircleOutlined />}
          loading={mutation.isPending}
          onClick={handleApprove}
        >
          {t("pendingTool.approve")}
        </Button>
        <Button
          danger
          size="small"
          icon={<CloseCircleOutlined />}
          loading={mutation.isPending}
          onClick={handleReject}
        >
          {t("pendingTool.reject")}
        </Button>
        <Button
          size="small"
          icon={<BulbOutlined />}
          loading={mutation.isPending}
          onClick={handleGuide}
        >
          {t("pendingTool.guide")}
        </Button>
      </Space>
    </div>
  );

  return (
    <Alert
      type="warning"
      showIcon
      icon={<ThunderboltOutlined />}
      style={{ marginBottom: 8 }}
      message={
        <Space size={6} wrap>
          <span>{t("pendingTool.title")}</span>
          <Typography.Text code>{pending.tool_name}</Typography.Text>
          {pending.tool_tier && <Tag color="orange">{pending.tool_tier}</Tag>}
          {pending.rule_id && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {pending.rule_id}
            </Typography.Text>
          )}
        </Space>
      }
      description={
        <div onClick={(e) => e.stopPropagation()}>
          {pending.reason && (
            <div style={{ fontSize: 12, color: token.colorTextSecondary, marginBottom: 6 }}>
              {pending.reason}
            </div>
          )}
          <Popover
            content={popover}
            trigger="click"
            open={open}
            onOpenChange={setOpen}
            placement="bottomLeft"
          >
            <Button type="primary" size="small" icon={<CheckCircleOutlined />}>
              {t("pendingTool.action")}
            </Button>
          </Popover>
        </div>
      }
    />
  );
}
