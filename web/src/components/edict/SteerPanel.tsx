import { useState } from "react";
import { Button, Input, Space, Typography, message } from "antd";
import { SendOutlined } from "@ant-design/icons";
import { steerEdict } from "../../api/edicts";
import GlowCard from "../common/GlowCard";
import { useT } from "../../i18n";

/** steer 中途注入(迭代 5)：长任务运行中向 actor 注入一条纠偏指示，下一轮吸收。 */
export default function SteerPanel({ edictId }: { edictId: string }) {
  const t = useT();
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!note.trim()) return;
    setLoading(true);
    try {
      await steerEdict(edictId, note.trim());
      message.success(t("steer.submitted"));
      setNote("");
    } catch {
      message.error(t("steer.failed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <GlowCard title={t("steer.title")} style={{ marginBottom: 24 }}>
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        {t("steer.hint")}
      </Typography.Paragraph>
      <Space.Compact style={{ width: "100%" }}>
        <Input.TextArea
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t("steer.placeholder")}
          style={{ resize: "vertical" }}
        />
        <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={submit}>
          {t("steer.send")}
        </Button>
      </Space.Compact>
    </GlowCard>
  );
}
