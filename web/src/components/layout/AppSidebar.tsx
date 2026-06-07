import {
  Layout,
  Menu,
  Button,
  Tooltip,
  theme,
} from "antd";
import {
  UnorderedListOutlined,
  PlusCircleOutlined,
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
      key: "/",
      icon: <UnorderedListOutlined />,
      label: t("nav.edictList"),
    },
    {
      key: "/edicts/create",
      icon: <PlusCircleOutlined />,
      label: t("nav.edictCreate"),
    },
    {
      key: "/approvals",
      icon: <AuditOutlined />,
      label: reviewCount > 0 ? `${t("nav.approvals")} (${reviewCount})` : t("nav.approvals"),
    },
    {
      key: "/scheduler",
      icon: <ScheduleOutlined />,
      label: t("nav.scheduler"),
    },
    {
      key: "/audit",
      icon: <SafetyCertificateOutlined />,
      label: t("nav.audit"),
    },
    {
      key: "/cost",
      icon: <DollarOutlined />,
      label: t("nav.tax"),
    },
    {
      key: "/memory",
      icon: <BookOutlined />,
      label: t("nav.knowledge"),
    },
    {
      key: "/consultation",
      icon: <TeamOutlined />,
      label: t("nav.consultation"),
    },
    {
      key: "/cabinet",
      icon: <CrownOutlined />,
      label: t("nav.cabinet"),
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
      key: "/personas",
      icon: <TeamOutlined />,
      label: t("nav.persona"),
    },
    {
      key: "/session-rules",
      icon: <SafetyOutlined />,
      label: t("nav.sessionRules"),
    },
    {
      key: "/universes",
      icon: <DeploymentUnitOutlined />,
      label: t("nav.universe"),
    },
    {
      key: "/system",
      icon: <ToolOutlined />,
      label: t("nav.system"),
    },
  ];

  // Note: DAG battle map is accessible via edict detail "查看作战图" button,
  // not as a direct sidebar item (it requires a dagId parameter).

  const selectedKey = location.pathname === "/" ? "/" : location.pathname;

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
