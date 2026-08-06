import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { Spin, Tabs } from "antd";
import { useSearchParams } from "react-router-dom";
import PageContainer from "../components/common/PageContainer";
import { useT } from "../i18n";

const SkillsTab = lazy(() => import("../components/system/SkillsTab"));
const ToolsTab = lazy(() => import("../components/system/ToolsTab"));
const MCPTab = lazy(() => import("../components/system/MCPTab"));
const SystemPromptTab = lazy(
  () => import("../components/system/SystemPromptTab"),
);
const ProvidersTab = lazy(() => import("../components/system/ProvidersTab"));
const PluginsTab = lazy(() => import("../components/system/PluginsTab"));
const GlobalConfigTab = lazy(
  () => import("../components/system/GlobalConfigTab"),
);
const ExternalCredentialsTab = lazy(
  () => import("../components/system/ExternalCredentialsTab"),
);
const EstopTab = lazy(() => import("../components/system/EstopTab"));

function lazyTab(children: ReactNode) {
  return (
    <Suspense
      fallback={<Spin size="large" style={{ display: "block", margin: 32 }} />}
    >
      {children}
    </Suspense>
  );
}

export default function SystemManagementPage() {
  const t = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const activeTab =
    !requestedTab || requestedTab === "secret-skills" ? "skills" : requestedTab;
  const setActiveTab = (key: string) =>
    setSearchParams({ tab: key }, { replace: true });
  return (
    <PageContainer title={t("system.title")}>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "skills",
            label: t("system.tab.skills"),
            children: lazyTab(<SkillsTab />),
          },
          {
            key: "tools",
            label: t("system.tab.tools"),
            children: lazyTab(<ToolsTab />),
          },
          {
            key: "mcp",
            label: t("system.tab.mcp"),
            children: lazyTab(<MCPTab />),
          },
          {
            key: "prompt",
            label: t("system.tab.prompt"),
            children: lazyTab(<SystemPromptTab />),
          },
          {
            key: "providers",
            label: t("system.tab.providers"),
            children: lazyTab(<ProvidersTab />),
          },
          {
            key: "plugins",
            label: t("system.tab.plugins"),
            children: lazyTab(<PluginsTab />),
          },
          {
            key: "config",
            label: t("system.tab.config"),
            children: lazyTab(<GlobalConfigTab />),
          },
          {
            key: "external-creds",
            label: t("system.tab.externalCreds"),
            children: lazyTab(<ExternalCredentialsTab />),
          },
          {
            key: "estop",
            label: t("system.tab.estop"),
            children: lazyTab(<EstopTab />),
          },
        ]}
      />
    </PageContainer>
  );
}
