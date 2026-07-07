import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { notification } from "antd";
import { createEdict } from "../api/edicts";
import EdictForm from "../components/edict/EdictForm";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import { useT } from "../i18n";
import type { EdictCreateRequest } from "../api/types";

export default function EdictCreatePage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const t = useT();

  const handleSubmit = async (values: EdictCreateRequest) => {
    setLoading(true);
    try {
      const res = await createEdict(values);
      if (res.success && res.data) {
        notification.success({
          message: t("page.edictCreate.successTitle"),
          description: t("page.edictCreate.successDesc"),
        });
        navigate(`/edicts/${res.data.id}`);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <PageContainer title={t("page.edictCreate.title")}>
      <GlowCard style={{ maxWidth: 720 }}>
        <EdictForm onSubmit={handleSubmit} loading={loading} />
      </GlowCard>
    </PageContainer>
  );
}
