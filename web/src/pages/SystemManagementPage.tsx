import { Tabs } from "antd";
import { useSearchParams } from "react-router-dom";
import PageContainer from "../components/common/PageContainer";
import SkillsTab from "../components/system/SkillsTab";
import SecretSkillsTab from "../components/system/SecretSkillsTab";
import ToolsTab from "../components/system/ToolsTab";
import MCPTab from "../components/system/MCPTab";
import SystemPromptTab from "../components/system/SystemPromptTab";
import ProvidersTab from "../components/system/ProvidersTab";
import PluginsTab from "../components/system/PluginsTab";
import GlobalConfigTab from "../components/system/GlobalConfigTab";
import ExternalCredentialsTab from "../components/system/ExternalCredentialsTab";
import { useT } from "../i18n";

export default function SystemManagementPage() {
  const t = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") ?? "skills";
  const setActiveTab = (key: string) => setSearchParams({ tab: key }, { replace: true });
  return (
    <PageContainer title={t("system.title")}>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "skills",
            label: t("system.tab.skills"),
            children: <SkillsTab />,
          },
          {
            key: "secret-skills",
            label: t("system.tab.secretSkills"),
            children: <SecretSkillsTab />,
          },
          {
            key: "tools",
            label: t("system.tab.tools"),
            children: <ToolsTab />,
          },
          {
            key: "mcp",
            label: t("system.tab.mcp"),
            children: <MCPTab />,
          },
          {
            key: "prompt",
            label: t("system.tab.prompt"),
            children: <SystemPromptTab />,
          },
          {
            key: "providers",
            label: t("system.tab.providers"),
            children: <ProvidersTab />,
          },
          {
            key: "plugins",
            label: t("system.tab.plugins"),
            children: <PluginsTab />,
          },
          {
            key: "config",
            label: t("system.tab.config"),
            children: <GlobalConfigTab />,
          },
          {
            key: "external-creds",
            label: t("system.tab.externalCreds"),
            children: <ExternalCredentialsTab />,
          },
        ]}
      />
    </PageContainer>
  );
}
