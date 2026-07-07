import { useState } from "react";
import { Modal, Form, Input, Select, Typography, theme } from "antd";
import { useCreateDecree } from "../../hooks/useApprovals";
import { useT } from "../../i18n";
import type { Memorial } from "../../api/types";

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
  const t = useT();

  const actionOptions = [
    { value: "approve", label: t("decree.actionOption.approve") },
    { value: "reject", label: t("decree.actionOption.reject") },
    { value: "retry", label: t("decree.actionOption.retry") },
    { value: "amend", label: t("decree.actionOption.amend") },
    { value: "cancel", label: t("decree.actionOption.cancel") },
  ];

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
      title={t("decree.title")}
      open={open}
      onOk={handleOk}
      onCancel={() => {
        form.resetFields();
        onClose();
      }}
      confirmLoading={mutation.isPending}
      okText={isCancel ? t("decree.okText.cancel") : t("decree.okText.submit")}
      cancelText={t("common.cancel")}
      okButtonProps={isCancel ? { danger: true } : undefined}
    >
      {memorial && (
        <div style={{ marginBottom: 16, padding: 12, background: "var(--ts-color-bg-subtle)", borderRadius: 6 }}>
          <Typography.Text style={{ color: token.colorTextSecondary, fontSize: 12 }}>
            {t("decree.memorialId")}: {memorial.id}
          </Typography.Text>
          {memorial.instruction && (
            <Typography.Paragraph style={{ color: token.colorText, marginTop: 4, marginBottom: 0, fontSize: 13 }}>
              {memorial.instruction}
            </Typography.Paragraph>
          )}
          {memorial.audit && (
            <div style={{ marginTop: 8 }}>
              <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
                {t("decree.auditResult")}：
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
          label={t("form.decree.field.action")}
          rules={[{ required: true }]}
        >
          <Select
            options={actionOptions}
            onChange={(v) => setSelectedAction(v)}
          />
        </Form.Item>

        {needsAmendedGoal && (
          <Form.Item
            name="amended_goal"
            label={t("form.decree.field.amendedGoal")}
            rules={[{ required: true, message: t("form.decree.validation.amendedGoalRequired") }]}
          >
            <Input.TextArea rows={3} placeholder={t("form.decree.placeholder.amendedGoal")} />
          </Form.Item>
        )}

        <Form.Item name="comment" label={t("form.decree.field.comment")}>
          <Input.TextArea rows={2} placeholder={t("form.decree.placeholder.comment")} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
