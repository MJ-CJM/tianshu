import type { ReactNode } from "react";
import type { MenuProps } from "antd";
import {
  AuditOutlined,
  BankOutlined,
  BookOutlined,
  CrownOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  DollarOutlined,
  ExperimentOutlined,
  GlobalOutlined,
  MessageOutlined,
  PlusCircleOutlined,
  RobotOutlined,
  SafetyOutlined,
  ScheduleOutlined,
  SettingOutlined,
  TeamOutlined,
  ToolOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import type { TFunction } from "../i18n";

interface NavigationItem {
  path: string;
  labelKey: string;
  icon: ReactNode;
  maturity?: "experimental" | "trial";
}

const TASK_ITEMS: readonly NavigationItem[] = [
  { path: "/approvals", labelKey: "nav.menu.allTasks", icon: <UnorderedListOutlined aria-hidden /> },
  { path: "/edicts/create", labelKey: "nav.menu.edictCreate", icon: <PlusCircleOutlined aria-hidden /> },
  { path: "/scheduler", labelKey: "nav.menu.scheduler", icon: <ScheduleOutlined aria-hidden /> },
  { path: "/audit", labelKey: "nav.menu.audit", icon: <AuditOutlined aria-hidden /> },
];

const COURT_ITEMS: readonly NavigationItem[] = [
  { path: "/personas", labelKey: "nav.menu.persona", icon: <TeamOutlined aria-hidden /> },
  { path: "/consultation", labelKey: "nav.menu.consultation", icon: <TeamOutlined aria-hidden /> },
  { path: "/cabinet", labelKey: "nav.menu.cabinet", icon: <CrownOutlined aria-hidden /> },
];

const OFFICE_ITEMS: readonly NavigationItem[] = [
  { path: "/memory", labelKey: "nav.menu.knowledge", icon: <BookOutlined aria-hidden /> },
  { path: "/hongluisi", labelKey: "nav.menu.foreign", icon: <GlobalOutlined aria-hidden /> },
  { path: "/tongzheng", labelKey: "nav.menu.notify", icon: <MessageOutlined aria-hidden /> },
];

const LAB_ITEMS: readonly NavigationItem[] = [
  {
    path: "/evolution",
    labelKey: "nav.menu.evolution",
    icon: <ExperimentOutlined aria-hidden />,
    maturity: "experimental",
  },
  {
    path: "/universes",
    labelKey: "nav.menu.universe",
    icon: <DeploymentUnitOutlined aria-hidden />,
    maturity: "experimental",
  },
  {
    path: "/evals",
    labelKey: "nav.menu.evals",
    icon: <ExperimentOutlined aria-hidden />,
    maturity: "trial",
  },
  {
    path: "/keqing",
    labelKey: "nav.menu.keqing",
    icon: <RobotOutlined aria-hidden />,
    maturity: "experimental",
  },
];

const SETTINGS_ITEMS: readonly NavigationItem[] = [
  { path: "/system", labelKey: "nav.menu.system", icon: <ToolOutlined aria-hidden /> },
  { path: "/session-rules", labelKey: "nav.menu.sessionRules", icon: <SafetyOutlined aria-hidden /> },
  { path: "/cost", labelKey: "nav.menu.tax", icon: <DollarOutlined aria-hidden /> },
];

function approvalsLabel(t: TFunction, reviewCount: number) {
  if (reviewCount <= 0) return t("nav.menu.tasks");
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      {t("nav.menu.tasks")}
      <span
        style={{
          background: "var(--ts-color-decision)",
          color: "var(--ts-color-accent-text-on)",
          borderRadius: 9,
          fontSize: 11,
          fontWeight: 600,
          lineHeight: "16px",
          padding: "0 6px",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {reviewCount}
      </span>
    </span>
  );
}

function maturityLabel(
  t: TFunction,
  labelKey: string,
  maturity?: NavigationItem["maturity"],
) {
  const label = t(labelKey);
  if (!maturity) return label;
  const maturityText = t(`nav.menu.maturity.${maturity}`);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span>{label}</span>
      <span
        style={{
          border: "1px solid currentColor",
          borderRadius: 8,
          fontSize: 10,
          lineHeight: "16px",
          padding: "0 5px",
          opacity: 0.75,
        }}
      >
        {maturityText}
      </span>
    </span>
  );
}

function childItems(t: TFunction, items: readonly NavigationItem[]) {
  return items.map((item) => ({
    key: item.path,
    icon: item.icon,
    label: maturityLabel(t, item.labelKey, item.maturity),
  }));
}

/**
 * Keep the product map shallow: six primary destinations and no third-level
 * section headers.
 */
export function buildSidebarItems(t: TFunction, reviewCount: number): MenuProps["items"] {
  return [
    {
      key: "/control",
      icon: <DashboardOutlined aria-hidden />,
      label: t("nav.menu.control"),
    },
    {
      key: "nav-tasks",
      icon: <ScheduleOutlined aria-hidden />,
      label: approvalsLabel(t, reviewCount),
      children: childItems(t, TASK_ITEMS),
    },
    {
      key: "nav-court",
      icon: <CrownOutlined aria-hidden />,
      label: t("nav.menu.court"),
      children: childItems(t, COURT_ITEMS),
    },
    {
      key: "nav-offices",
      icon: <BankOutlined aria-hidden />,
      label: t("nav.menu.offices"),
      children: childItems(t, OFFICE_ITEMS),
    },
    {
      key: "nav-lab",
      icon: <ExperimentOutlined aria-hidden />,
      label: maturityLabel(t, "nav.menu.laboratory", "experimental"),
      children: childItems(t, LAB_ITEMS),
    },
    {
      key: "nav-settings",
      icon: <SettingOutlined aria-hidden />,
      label: t("nav.menu.settings"),
      children: childItems(t, SETTINGS_ITEMS),
    },
  ];
}

type SidebarSectionKey =
  | "nav-tasks"
  | "nav-court"
  | "nav-offices"
  | "nav-lab"
  | "nav-settings";

const SECTION_PATHS: ReadonlyArray<{
  key: SidebarSectionKey;
  roots: readonly string[];
}> = [
  {
    key: "nav-tasks",
    roots: ["/approvals", "/edicts", "/scheduler", "/audit", "/dag"],
  },
  {
    key: "nav-court",
    roots: ["/personas", "/consultation", "/cabinet"],
  },
  {
    key: "nav-offices",
    roots: ["/memory", "/hongluisi", "/tongzheng"],
  },
  {
    key: "nav-lab",
    roots: ["/evolution", "/universes", "/evals", "/keqing"],
  },
  {
    key: "nav-settings",
    roots: ["/system", "/session-rules", "/cost"],
  },
];

function normalizePath(pathname: string) {
  return pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
}

function matchesRoot(pathname: string, root: string) {
  return pathname === root || pathname.startsWith(`${root}/`);
}

export function sidebarSectionForPath(pathname: string): SidebarSectionKey | null {
  const normalizedPath = normalizePath(pathname);
  return (
    SECTION_PATHS.find(({ roots }) =>
      roots.some((root) => matchesRoot(normalizedPath, root)),
    )?.key ?? null
  );
}

export function sidebarSelectedKey(pathname: string) {
  const normalizedPath = normalizePath(pathname);
  if (normalizedPath === "/edicts/create") return normalizedPath;
  if (
    matchesRoot(normalizedPath, "/edicts") ||
    matchesRoot(normalizedPath, "/dag")
  ) {
    return "/approvals";
  }
  if (matchesRoot(normalizedPath, "/personas")) return "/personas";
  return normalizedPath;
}
