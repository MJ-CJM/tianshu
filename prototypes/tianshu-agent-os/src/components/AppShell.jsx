import { useState } from "react";
import {
  AppstoreOutlined,
  AuditOutlined,
  BookOutlined,
  CloseOutlined,
  CrownOutlined,
  DeploymentUnitOutlined,
  DollarOutlined,
  ExperimentOutlined,
  GlobalOutlined,
  MenuOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  MoonOutlined,
  SafetyCertificateOutlined,
  SafetyOutlined,
  ScheduleOutlined,
  SunOutlined,
  TeamOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { NAV_GROUPS } from "../data/mockData.js";
import brandLogo from "../../../../web/public/brand.png";

const ICONS = {
  audit: AuditOutlined,
  schedule: ScheduleOutlined,
  crown: CrownOutlined,
  team: TeamOutlined,
  certificate: SafetyCertificateOutlined,
  safety: SafetyOutlined,
  officials: TeamOutlined,
  book: BookOutlined,
  universe: DeploymentUnitOutlined,
  experiment: ExperimentOutlined,
  tool: ToolOutlined,
  global: GlobalOutlined,
  message: MessageOutlined,
  cost: DollarOutlined,
};

function DepartmentNavigation({ currentScreen, onNavigate }) {
  const activeDepartment = currentScreen === "edict" ? "御书房" : currentScreen === "evolution" ? "位面" : null;

  return (
    <nav className="department-nav" aria-label="天枢部门">
      <button
        className={`nav-control ${currentScreen === "control" ? "is-active" : ""}`}
        type="button"
        aria-label="中枢总览"
        aria-current={currentScreen === "control" ? "page" : undefined}
        onClick={() => onNavigate("control", "中枢总览")}
      >
        <AppstoreOutlined aria-hidden="true" />
        <span className="nav-label">中枢总览</span>
      </button>

      <div className="nav-separator" />

      {NAV_GROUPS.map((group) => (
        <section className="nav-group" key={group.label}>
          <div className="nav-group-title">{group.label}</div>
          <div className="nav-group-items">
            {group.items.map((item) => {
              const Icon = ICONS[item.icon];
              const active = activeDepartment === item.label;
              return (
                <button
                  className={`nav-item ${active ? "is-active" : ""}`}
                  type="button"
                  key={item.label}
                  aria-label={item.label}
                  aria-current={active ? "page" : undefined}
                  onClick={() => onNavigate(item.target, item.label)}
                >
                  <Icon aria-hidden="true" />
                  <span className="nav-label">{item.label}</span>
                  {item.count ? <span className="nav-count">{item.count}</span> : null}
                </button>
              );
            })}
          </div>
        </section>
      ))}
    </nav>
  );
}

export function AppShell({
  children,
  currentScreen,
  dark,
  mobileOpen,
  onCloseMobile,
  onNavigate,
  onOpenMobile,
  onToggleTheme,
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [locale, setLocale] = useState("zh-classic");

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <button className="mobile-menu-button" type="button" aria-label="打开部门导航" onClick={onOpenMobile}>
            <MenuOutlined aria-hidden="true" />
          </button>
          <button className="brand-button" type="button" onClick={() => onNavigate("control", "中枢总览")}>
            <img className="brand-logo" src={brandLogo} alt="天枢 Logo" />
            <span className="brand-name">天枢</span>
          </button>
        </div>

        <div className="topbar-tagline">成功只有一个——按照自己的方式，去度过人生。</div>

        <div className="topbar-meta" aria-label="系统状态">
          <div className="locale-switcher" role="group" aria-label="语言模式">
            {[
              ["zh-classic", "彩蛋"],
              ["zh-modern", "通用"],
              ["en", "English"],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={locale === value}
                className={locale === value ? "is-active" : ""}
                onClick={() => setLocale(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="connection-state"><span className="connection-dot" />实时</span>
          <span className="connection-state"><span className="connection-dot" />通政</span>
        </div>
      </header>

      <div className={`workspace-shell ${collapsed ? "is-collapsed" : ""}`}>
        <aside className={`desktop-sidebar ${collapsed ? "is-collapsed" : ""}`}>
          <DepartmentNavigation currentScreen={currentScreen} onNavigate={onNavigate} />
          <div className="sidebar-controls">
            <button
              className="sidebar-control"
              type="button"
              aria-label={dark ? "浅色模式" : "深色模式"}
              title={collapsed ? (dark ? "浅色模式" : "深色模式") : undefined}
              onClick={onToggleTheme}
            >
              {dark ? <SunOutlined aria-hidden="true" /> : <MoonOutlined aria-hidden="true" />}
              <span className="sidebar-control-label">{dark ? "浅色模式" : "深色模式"}</span>
            </button>
            <button
              className="sidebar-control"
              type="button"
              aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
              title={collapsed ? "展开侧栏" : undefined}
              onClick={() => setCollapsed((value) => !value)}
            >
              {collapsed ? <MenuUnfoldOutlined aria-hidden="true" /> : <MenuFoldOutlined aria-hidden="true" />}
              <span className="sidebar-control-label">{collapsed ? "展开侧栏" : "收起侧栏"}</span>
            </button>
          </div>
        </aside>

        <main className="main-surface" key={currentScreen}>{children}</main>
      </div>

      {mobileOpen ? (
        <div className="mobile-drawer-layer">
          <button className="drawer-backdrop" type="button" aria-label="关闭导航遮罩" onClick={onCloseMobile} />
          <aside className="mobile-drawer" role="dialog" aria-modal="true" aria-label="部门导航">
            <div className="drawer-header">
              <span>部门导航</span>
              <button className="icon-button" type="button" aria-label="关闭部门导航" onClick={onCloseMobile}>
                <CloseOutlined aria-hidden="true" />
              </button>
            </div>
            <DepartmentNavigation currentScreen={currentScreen} onNavigate={onNavigate} />
          </aside>
        </div>
      ) : null}
    </div>
  );
}
