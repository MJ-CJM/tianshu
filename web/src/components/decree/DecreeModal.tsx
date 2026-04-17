import { useState } from "react";
import { Modal, Form, Input, Select, Typography, theme } from "antd";
import { useCreateDecree } from "../../hooks/useApprovals";
import type { Memorial } from "../../api/types";

const ACTION_OPTIONS = [
  { value: "approve", label: "准 — 批准执行" },
  { value: "reject", label: "驳 — 驳回" },
  { value: "retry", label: "重办 — 原目标重试" },
  { value: "amend", label: "改批 — 修改目标后重试" },
  { value: "cancel", label: "撤回 — 取消任务" },
];

interface DecreeModalProps {
  memorial: Memorial | null;
  action: string | null;
  open: boolean;
  onClose: () => void;
}

export default function DecreeModal({ memorial, action, open, onClose }: DecreeModalProps) {
  const { token } = theme.useToken();
  const [form] = Form.useForm();
  const mutation = useCreateDecree();
  const [selectedAction, setSelectedAction] = useState(action ?? "approve");

  const handleOk = () => {
    form.validateFields().then((values) => {
      if (!memorial) return;

      mutation.mutate(
        {
          memorial_id: memorial.id,
          action: values.action,
          comment: values.comment || undefined,
          amended_goal: values.amended_goal || undefined,
          actor: "user",
        },
        {
          onSuccess: () => {
            form.resetFields();
            onClose();
          },
        },
      );
    });
  };

  const currentAction = selectedAction;
  const needsAmendedGoal = currentAction === "amend";
  const isCancel = currentAction === "cancel";

  return (
    <Modal
      title="批红"
      open={open}
      onOk={handleOk}
      onCancel={() => {
        form.resetFields();
        onClose();
      }}
      confirmLoading={mutation.isPending}
      okText={isCancel ? "确认撤回" : "提交"}
      cancelText="取消"
      okButtonProps={isCancel ? { danger: true } : undefined}
    >
      {memorial && (
        <div style={{ marginBottom: 16, padding: 12, background: "var(--ts-color-bg-subtle)", borderRadius: 6 }}>
          <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
            奏折 ID: {memorial.id}
          </Typography.Text>
          {memorial.instruction && (
            <Typography.Paragraph style={{ color: token.colorText, marginTop: 4, marginBottom: 0, fontSize: 13 }}>
              {memorial.instruction}
            </Typography.Paragraph>
          )}
          {memorial.audit && (
            <div style={{ marginTop: 8 }}>
              <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
                审计结果：
              </Typography.Text>
              {memorial.audit.reasons.map((r, i) => (
                <div key={i} style={{ fontSize: 12, color: token.colorWarning }}>{r}</div>
              ))}
            </div>
          )}
        </div>
      )}

      <Form
        form={form}
        layout="vertical"
        initialValues={{ action: action ?? "approve" }}
      >
        <Form.Item
          name="action"
          label="操作"
          rules={[{ required: true }]}
        >
          <Select
            options={ACTION_OPTIONS}
            onChange={(v) => setSelectedAction(v)}
          />
        </Form.Item>

        {needsAmendedGoal && (
          <Form.Item
            name="amended_goal"
            label="修改后目标"
            rules={[{ required: true, message: "改批操作须填写修改后目标" }]}
          >
            <Input.TextArea rows={3} placeholder="请输入修改后的任务目标" />
          </Form.Item>
        )}

        <Form.Item name="comment" label="批注（可选）">
          <Input.TextArea rows={2} placeholder="可选批注" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
