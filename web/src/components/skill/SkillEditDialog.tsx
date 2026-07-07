import { useState, useEffect } from "react";
import { Modal, Input, Spin, notification } from "antd";
import { getSkill, updateSkill } from "../../api/system";
import { useT } from "../../i18n";

const monoStyle: React.CSSProperties = {
  fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
  fontSize: 13,
};

interface SkillEditDialogProps {
  name: string | null;
  onClose: () => void;
  onSaved: () => void;
}

export default function SkillEditDialog({ name, onClose, onSaved }: SkillEditDialogProps) {
  const t = useT();
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!name) return;
    setLoading(true);
    setContent("");
    getSkill(name)
      .then((res) => {
        setContent(res.data?.content ?? "");
      })
      .finally(() => setLoading(false));
  }, [name]);

  const handleSave = async () => {
    if (!name) return;
    setSaving(true);
    try {
      await updateSkill(name, content);
      notification.success({ message: t("skillsPage.toast.editSaved") });
      onSaved();
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={name ? t("skillsPage.editDialog.titleWithName", { name }) : t("skillsPage.editDialog.title")}
      open={!!name}
      onCancel={onClose}
      onOk={handleSave}
      confirmLoading={saving}
      okText={t("button.save")}
      cancelText={t("common.cancel")}
      width={720}
      destroyOnClose
    >
      {loading ? (
        <Spin />
      ) : (
        <Input.TextArea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          autoSize={{ minRows: 20, maxRows: 40 }}
          style={monoStyle}
        />
      )}
    </Modal>
  );
}
