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
} from "@ant-design/icons";
import { useNavigate, useLocation } from "react-router-dom";
import { useState } from "react";
import { useTheme } from "../../hooks/useTheme";
import { useNeedsReview } from "../../hooks/useApprovals";

const staticMenuItems = [
  {
    key: "/",
    icon: <UnorderedListOutlined />,
    label: "敕令总览",
  },
  {
    key: "/edicts/create",
    icon: <PlusCircleOutlined />,
    label: "颁发敕令",
  },
];

export default function AppSidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { token } = theme.useToken();
  const { mode, toggleTheme } = useTheme();
  const [collapsed, setCollapsed] = useState(false);

  const { data: reviewData } = useNeedsReview();
  const reviewCount = reviewData?.metadata?.total ?? reviewData?.data?.length ?? 0;

  const menuItems = [
    ...staticMenuItems,
    {
      key: "/approvals",
      icon: <AuditOutlined />,
      label: reviewCount > 0 ? `批红台 (${reviewCount})` : "批红台",
    },
    {
      key: "/scheduler",
      icon: <ScheduleOutlined />,
      label: "文书房",
    },
    {
      key: "/audit",
      icon: <SafetyCertificateOutlined />,
      label: "都察院",
    },
    {
      key: "/cost",
      icon: <DollarOutlined />,
      label: "户部账房",
    },
    {
      key: "/memory",
      icon: <BookOutlined />,
      label: "文渊阁",
    },
    {
      key: "/consultation",
      icon: <TeamOutlined />,
      label: "廷议",
    },
    {
      key: "/cabinet",
      icon: <CrownOutlined />,
      label: "内阁",
    },
    {
      key: "/hongluisi",
      icon: <GlobalOutlined />,
      label: "鸿胪寺",
    },
    {
      key: "/tongzheng",
      icon: <MessageOutlined />,
      label: "通政司",
    },
    {
      key: "/personas",
      icon: <TeamOutlined />,
      label: "百官阁",
    },
    {
      key: "/session-rules",
      icon: <SafetyOutlined />,
      label: "权印司",
    },
    {
      key: "/system",
      icon: <ToolOutlined />,
      label: "藏兵阁",
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
            title={collapsed ? (mode === "light" ? "深色模式" : "浅色模式") : ""}
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
              {collapsed ? null : mode === "light" ? "深色模式" : "浅色模式"}
            </Button>
          </Tooltip>
          <Tooltip
            title={collapsed ? (collapsed ? "展开" : "收起") : ""}
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
              {collapsed ? null : "收起侧栏"}
            </Button>
          </Tooltip>
        </div>
      </div>
    </Layout.Sider>
  );
}
