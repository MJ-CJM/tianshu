import type { ReactNode } from "react";
import type { MenuProps } from "antd";
import {
  AuditOutlined,
  BookOutlined,
  CrownOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  DollarOutlined,
  ExperimentOutlined,
  GlobalOutlined,
  MessageOutlined,
  SafetyCertificateOutlined,
  SafetyOutlined,
  ScheduleOutlined,
  TeamOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import type { TFunction } from "../i18n";

interface DepartmentDefinition {
  path: string;
  labelKey: string;
  icon: ReactNode;
}

interface DepartmentGroupDefinition {
  key: string;
  labelKey: string;
  departments: readonly DepartmentDefinition[];
}

export const CONTROL_ITEM: DepartmentDefinition = {
  path: "/control",
  labelKey: "nav.control",
  icon: <DashboardOutlined aria-hidden />,
};

export const DEPARTMENT_GROUPS: readonly DepartmentGroupDefinition[] = [
  {
    key: "group-edict",
    labelKey: "nav.group.edict",
    departments: [
      { path: "/approvals", labelKey: "nav.approvals", icon: <AuditOutlined aria-hidden /> },
      { path: "/scheduler", labelKey: "nav.scheduler", icon: <ScheduleOutlined aria-hidden /> },
    ],
  },
  {
    key: "group-gov",
    labelKey: "nav.group.gov",
    departments: [
      { path: "/cabinet", labelKey: "nav.cabinet", icon: <CrownOutlined aria-hidden /> },
      { path: "/consultation", labelKey: "nav.consultation", icon: <TeamOutlined aria-hidden /> },
      { path: "/audit", labelKey: "nav.audit", icon: <SafetyCertificateOutlined aria-hidden /> },
      { path: "/session-rules", labelKey: "nav.sessionRules", icon: <SafetyOutlined aria-hidden /> },
    ],
  },
  {
    key: "group-growth",
    labelKey: "nav.group.growth",
    departments: [
      { path: "/personas", labelKey: "nav.persona", icon: <TeamOutlined aria-hidden /> },
      { path: "/memory", labelKey: "nav.knowledge", icon: <BookOutlined aria-hidden /> },
      { path: "/universes", labelKey: "nav.universe", icon: <DeploymentUnitOutlined aria-hidden /> },
      { path: "/evals", labelKey: "nav.evals", icon: <ExperimentOutlined aria-hidden /> },
    ],
  },
  {
    key: "group-system",
    labelKey: "nav.group.system",
    departments: [
      { path: "/system", labelKey: "nav.system", icon: <ToolOutlined aria-hidden /> },
      { path: "/hongluisi", labelKey: "nav.foreign", icon: <GlobalOutlined aria-hidden /> },
      { path: "/tongzheng", labelKey: "nav.notify", icon: <MessageOutlined aria-hidden /> },
      { path: "/cost", labelKey: "nav.tax", icon: <DollarOutlined aria-hidden /> },
    ],
  },
];

function approvalsLabel(t: TFunction, reviewCount: number) {
  if (reviewCount <= 0) return t("nav.approvals");
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      {t("nav.approvals")}
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

export function buildSidebarItems(t: TFunction, reviewCount: number): MenuProps["items"] {
  return [
    {
      key: CONTROL_ITEM.path,
      icon: CONTROL_ITEM.icon,
      label: t(CONTROL_ITEM.labelKey),
    },
    ...DEPARTMENT_GROUPS.map((group) => ({
      key: group.key,
      type: "group" as const,
      label: t(group.labelKey),
      children: group.departments.map((department) => ({
        key: department.path,
        icon: department.icon,
        label:
          department.path === "/approvals"
            ? approvalsLabel(t, reviewCount)
            : t(department.labelKey),
      })),
    })),
  ];
}
