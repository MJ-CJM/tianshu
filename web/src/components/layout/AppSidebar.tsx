import { Button, Layout, Menu, Tooltip, theme } from "antd";
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MoonOutlined,
  SunOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState, type KeyboardEvent } from "react";
import { useTheme } from "../../hooks/useTheme";
import { useNeedsReview } from "../../hooks/useApprovals";
import { useSidebarState } from "../../hooks/useSidebarState";
import { useT } from "../../i18n";
import {
  buildSidebarItems,
  sidebarSectionForPath,
  sidebarSelectedKey,
} from "../../navigation/departments";

function moveMenuFocus(event: KeyboardEvent<HTMLElement>) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const items = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])'),
  );
  if (items.length === 0) return;
  event.preventDefault();
  event.stopPropagation();
  const current = items.indexOf(document.activeElement as HTMLElement);
  const next = event.key === "Home"
    ? 0
    : event.key === "End"
      ? items.length - 1
      : event.key === "ArrowDown"
        ? (current + 1 + items.length) % items.length
        : current < 0
          ? items.length - 1
          : (current - 1 + items.length) % items.length;
  items[next]?.focus();
}

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
  const routeSection = sidebarSectionForPath(location.pathname);
  // 展开态是「集合」而非单值：收不收由用户决定，别替他手风琴（issue #71）。
  // 也不再绑 location.key——侧边栏本就是跨页面导航用的，展开状态该撑过跳转。
  const [openSections, setOpenSections] = useState<string[]>(() =>
    routeSection ? [routeSection] : [],
  );
  // 进入某页面时其所属分组自动展开，但不以收起其他分组为代价
  useEffect(() => {
    if (!routeSection) return;
    setOpenSections((prev) => (prev.includes(routeSection) ? prev : [...prev, routeSection]));
  }, [routeSection]);
  const handleCollapsedChange = (nextCollapsed: boolean) => {
    setCollapsed(nextCollapsed);
  };

  return (
    <Layout.Sider
      collapsible
      collapsed={collapsed}
      onCollapse={handleCollapsedChange}
      trigger={null}
      width={200}
      collapsedWidth={60}
      style={{ borderRight: `1px solid ${token.colorBorder}` }}
    >
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <Menu
          aria-label={t("sidebar.primaryNavigation")}
          tabIndex={0}
          onKeyDownCapture={moveMenuFocus}
          mode="inline"
          inlineCollapsed={collapsed}
          selectedKeys={[sidebarSelectedKey(location.pathname)]}
          openKeys={collapsed ? undefined : openSections}
          onOpenChange={(keys) => {
            if (collapsed) return;
            setOpenSections(keys.map(String));
          }}
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
              onClick={() => handleCollapsedChange(!collapsed)}
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
