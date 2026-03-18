import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { notification } from "antd";
import { createEdict } from "../api/edicts";
import EdictForm from "../components/edict/EdictForm";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";

export default function EdictCreatePage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (values: {
    goal: string;
    context?: string;
  }) => {
    setLoading(true);
    try {
      const res = await createEdict(values);
      if (res.success && res.data) {
        notification.success({
          message: "敕令已呈递",
          description: "天枢正在办理",
        });
        navigate(`/edicts/${res.data.id}`);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <PageContainer title="颁发新敕令">
      <GlowCard style={{ maxWidth: 720 }}>
        <EdictForm onSubmit={handleSubmit} loading={loading} />
      </GlowCard>
    </PageContainer>
  );
}
