import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { notification } from "antd";
import { createEdict } from "../api/edicts";
import EdictForm from "../components/edict/EdictForm";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import { useT } from "../i18n";
import type { EdictCreateRequest } from "../api/types";
import { ONBOARDING_QUERY_KEY, type OnboardingState } from "../api/onboarding";

export function EdictCreationForm({
  governanceConfirmation = "advisory",
}: {
  governanceConfirmation?: "advisory" | "always";
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [loading, setLoading] = useState(false);
  const t = useT();

  const handleSubmit = async (values: EdictCreateRequest) => {
    setLoading(true);
    try {
      const res = await createEdict(values);
      if (res.success && res.data) {
        await queryClient.cancelQueries({ queryKey: ONBOARDING_QUERY_KEY, exact: true });
        queryClient.setQueryData<OnboardingState>(ONBOARDING_QUERY_KEY, (current) =>
          current ? { ...current, required: false } : current,
        );
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
    <GlowCard style={{ maxWidth: 720 }}>
      <EdictForm
        onSubmit={handleSubmit}
        loading={loading}
        governanceConfirmation={governanceConfirmation}
      />
    </GlowCard>
  );
}

export default function EdictCreatePage() {
  const t = useT();
  return (
    <PageContainer title={t("page.edictCreate.title")}>
      <EdictCreationForm />
    </PageContainer>
  );
}
