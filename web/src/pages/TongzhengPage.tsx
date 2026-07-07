import PageContainer from "../components/common/PageContainer";
import InstanceManager from "../components/config/InstanceManager";
import { useT } from "../i18n";

export default function TongzhengPage() {
  const t = useT();
  return (
    <PageContainer title={t("tongzheng.title")}>
      <InstanceManager />
    </PageContainer>
  );
}
