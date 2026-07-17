import { Button, Layout, Menu, Tooltip, theme } from "antd";
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MoonOutlined,
  SunOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate } from "react-router-dom";
import { useTheme } from "../../hooks/useTheme";
import { useNeedsReview } from "../../hooks/useApprovals";
import { useSidebarState } from "../../hooks/useSidebarState";
import { useT } from "../../i18n";
import { buildSidebarItems } from "../../navigation/departments";

export default function AppSidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { token } = theme.useToken();
  const { mode, toggleTheme } = useTheme();
  const { collapsed, setCollapsed } = useSidebarState();
  const t = useT();
  const { data: reviewData } = useNeedsReview();
  const reviewCount = reviewData?.metadata?.total ?? reviewData?.data?.length ?? 0;
  const themeAction = mode === "light" ? t("sidebar.switchToDark") : t("sidebar.switchToLight");
  const collapseAction = collapsed ? t("sidebar.expand") : t("sidebar.collapse");

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
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <Menu
          mode="inline"
          inlineCollapsed={collapsed}
          selectedKeys={[location.pathname]}
          items={buildSidebarItems(t, reviewCount)}
          onClick={({ key }) => navigate(key)}
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            paddingTop: 12,
            borderRight: "none",
          }}
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
          <Tooltip title={collapsed ? themeAction : ""} placement="right">
            <Button
              type="text"
              aria-label={themeAction}
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
          <Tooltip title={collapsed ? collapseAction : ""} placement="right">
            <Button
              type="text"
              aria-label={collapseAction}
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed(!collapsed)}
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
