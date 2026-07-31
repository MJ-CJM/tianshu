import { Button } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import PageContainer from "../components/common/PageContainer";
import AllEdictsView from "../components/study/AllEdictsView";
import { useT } from "../i18n";

export default function RoyalStudyPage() {
  const navigate = useNavigate();
  const t = useT();

  return (
    <PageContainer
      title={t("nav.tasks")}
      extra={
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => navigate("/edicts/create")}
        >
          {t("nav.edictCreate")}
        </Button>
      }
    >
      <AllEdictsView />
    </PageContainer>
  );
}
