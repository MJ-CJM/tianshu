import {
  Layout,
  Menu,
  Button,
  Tooltip,
  theme,
} from "antd";
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SunOutlined,
  MoonOutlined,
  AuditOutlined,
  ScheduleOutlined,
  SafetyCertificateOutlined,
  DollarOutlined,
  BookOutlined,
  TeamOutlined,
  CrownOutlined,
  ToolOutlined,
  SafetyOutlined,
  GlobalOutlined,
  MessageOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
} from "@ant-design/icons";
import { useNavigate, useLocation } from "react-router-dom";
import { useState } from "react";
import { useTheme } from "../../hooks/useTheme";
import { useNeedsReview } from "../../hooks/useApprovals";
import { useT } from "../../i18n";

export default function AppSidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { token } = theme.useToken();
  const { mode, toggleTheme } = useTheme();
  const [collapsed, setCollapsed] = useState(false);
  const t = useT();

  const { data: reviewData } = useNeedsReview();
  const reviewCount = reviewData?.metadata?.total ?? reviewData?.data?.length ?? 0;

  const menuItems = [
    {
      key: "group-edict",
      type: "group" as const,
      label: t("nav.group.edict"),
      children: [
        {
          key: "/",
          icon: <AuditOutlined />,
          label: reviewCount > 0 ? `${t("nav.approvals")} (${reviewCount})` : t("nav.approvals"),
        },
        {
          key: "/scheduler",
          icon: <ScheduleOutlined />,
          label: t("nav.scheduler"),
        },
      ],
    },
    {
      key: "group-gov",
      type: "group" as const,
      label: t("nav.group.gov"),
      children: [
        {
          key: "/cabinet",
          icon: <CrownOutlined />,
          label: t("nav.cabinet"),
        },
        {
          key: "/consultation",
          icon: <TeamOutlined />,
          label: t("nav.consultation"),
        },
        {
          key: "/audit",
          icon: <SafetyCertificateOutlined />,
          label: t("nav.audit"),
        },
        {
          key: "/session-rules",
          icon: <SafetyOutlined />,
          label: t("nav.sessionRules"),
        },
      ],
    },
    {
      key: "group-growth",
      type: "group" as const,
      label: t("nav.group.growth"),
      children: [
        {
          key: "/personas",
          icon: <TeamOutlined />,
          label: t("nav.persona"),
        },
        {
          key: "/memory",
          icon: <BookOutlined />,
          label: t("nav.knowledge"),
        },
        {
          key: "/universes",
          icon: <DeploymentUnitOutlined />,
          label: t("nav.universe"),
        },
        {
          key: "/evals",
          icon: <ExperimentOutlined />,
          label: t("nav.evals"),
        },
      ],
    },
    {
      key: "group-system",
      type: "group" as const,
      label: t("nav.group.system"),
      children: [
        {
          key: "/system",
          icon: <ToolOutlined />,
          label: t("nav.system"),
        },
        {
          key: "/hongluisi",
          icon: <GlobalOutlined />,
          label: t("nav.foreign"),
        },
        {
          key: "/tongzheng",
          icon: <MessageOutlined />,
          label: t("nav.notify"),
        },
        {
          key: "/cost",
          icon: <DollarOutlined />,
          label: t("nav.tax"),
        },
      ],
    },
  ];

  // Note: DAG battle map is accessible via edict detail "查看作战图" button,
  // not as a direct sidebar item (it requires a dagId parameter).

  // "/" 与 "/approvals" 均为御书房（合并页两个路径，避免书签失效），高亮同一菜单项
  const selectedKey = location.pathname === "/approvals" ? "/" : location.pathname;

  return (
    <Layout.Sider
      collapsible
      collapsed={collapsed}
      onCollapse={setCollapsed}
      trigger={null}
      width={200}
      collapsedWidth={60}
      style={{ borderRight: `1px solid ${token.colorBorder}` }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
        }}
      >
        <Menu
          mode="inline"
          inlineCollapsed={collapsed}
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ flex: 1, paddingTop: 12, borderRight: "none" }}
        />

        <div
          style={{
            borderTop: `1px solid ${token.colorBorder}`,
            padding: collapsed ? "8px 0" : "8px 12px",
            display: "flex",
            flexDirection: "column",
            alignItems: collapsed ? "center" : "stretch",
            gap: 4,
          }}
        >
          <Tooltip
            title={collapsed ? (mode === "light" ? t("sidebar.darkMode") : t("sidebar.lightMode")) : ""}
            placement="right"
          >
            <Button
              type="text"
              icon={mode === "light" ? <MoonOutlined /> : <SunOutlined />}
              onClick={toggleTheme}
              style={{
                color: token.colorText,
                width: collapsed ? 40 : "100%",
                justifyContent: collapsed ? "center" : "flex-start",
              }}
            >
              {collapsed ? null : mode === "light" ? t("sidebar.darkMode") : t("sidebar.lightMode")}
            </Button>
          </Tooltip>
          <Tooltip
            title={collapsed ? t("sidebar.expand") : ""}
            placement="right"
          >
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((v) => !v)}
              style={{
                color: token.colorTextSecondary,
                width: collapsed ? 40 : "100%",
                justifyContent: collapsed ? "center" : "flex-start",
              }}
            >
              {collapsed ? null : t("sidebar.collapse")}
            </Button>
          </Tooltip>
        </div>
      </div>
    </Layout.Sider>
  );
}
