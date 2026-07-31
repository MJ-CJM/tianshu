import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { notification } from "antd";
import { createEdict } from "../api/edicts";
import EdictForm from "../components/edict/EdictForm";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import { useT } from "../i18n";
import type { EdictCreateRequest } from "../api/types";

export function EdictCreationForm({
  governanceConfirmation = "advisory",
}: {
  governanceConfirmation?: "advisory" | "always";
}) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [loading, setLoading] = useState(false);
  const t = useT();
  const requestedScheduleMode = searchParams.get("schedule");
  const initialScheduleMode =
    requestedScheduleMode === "once" || requestedScheduleMode === "cron"
      ? requestedScheduleMode
      : "immediate";

  const handleSubmit = async (values: EdictCreateRequest) => {
    setLoading(true);
    try {
      const res = await createEdict(values);
      if (res.success && res.data) {
        const edictId = res.data.id;
        navigate(`/edicts/${edictId}`, { flushSync: true });
        notification.success({
          message: t("page.edictCreate.successTitle"),
          description: t("page.edictCreate.successDesc"),
        });
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <GlowCard style={{ width: "100%" }}>
      <EdictForm
        onSubmit={handleSubmit}
        loading={loading}
        governanceConfirmation={governanceConfirmation}
        initialScheduleMode={initialScheduleMode}
      />
    </GlowCard>
  );
}

export default function EdictCreatePage() {
  const t = useT();
  return (
    <PageContainer title={t("page.edictCreate.title")} contentMaxWidth={960}>
      <EdictCreationForm />
    </PageContainer>
  );
}
