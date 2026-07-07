import { Button, Tabs } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import PageContainer from "../components/common/PageContainer";
import PendingView from "../components/study/PendingView";
import AllEdictsView from "../components/study/AllEdictsView";
import { useT } from "../i18n";

export default function RoyalStudyPage() {
  const navigate = useNavigate();
  const t = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") ?? "pending";
  const setActiveTab = (key: string) => setSearchParams({ tab: key }, { replace: true });

  return (
    <PageContainer
      title={t("nav.approvals")}
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
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "pending",
            label: t("study.tab.pending"),
            children: <PendingView active={activeTab === "pending"} />,
          },
          {
            key: "all",
            label: t("study.tab.all"),
            children: <AllEdictsView />,
          },
        ]}
      />
    </PageContainer>
  );
}
