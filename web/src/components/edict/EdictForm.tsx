import { Form, Input, Button } from "antd";
import { SendOutlined } from "@ant-design/icons";

interface EdictFormValues {
  goal: string;
  context?: string;
}

interface EdictFormProps {
  onSubmit: (values: EdictFormValues) => void;
  loading: boolean;
}

export default function EdictForm({ onSubmit, loading }: EdictFormProps) {
  const [form] = Form.useForm<EdictFormValues>();

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={onSubmit}
      requiredMark={false}
      style={{ maxWidth: 640 }}
    >
      <Form.Item
        name="goal"
        label="敕令旨意"
        rules={[{ required: true, message: "请拟定敕令旨意" }]}
      >
        <Input.TextArea
          rows={4}
          placeholder="请拟定敕令旨意..."
          style={{ resize: "vertical" }}
        />
      </Form.Item>

      <Form.Item name="context" label="附则（可选）">
        <Input.TextArea
          rows={3}
          placeholder="补充背景信息或约束条件..."
          style={{ resize: "vertical" }}
        />
      </Form.Item>

      <Form.Item>
        <Button
          type="primary"
          htmlType="submit"
          loading={loading}
          icon={<SendOutlined />}
          size="large"
        >
          颁发敕令
        </Button>
      </Form.Item>
    </Form>
  );
}
