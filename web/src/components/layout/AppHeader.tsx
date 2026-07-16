import { Button, Layout, Tooltip, theme } from "antd";
import { LogoutOutlined, UserOutlined } from "@ant-design/icons";
import HealthDot from "../common/HealthDot";
import ConnectionIndicator from "../common/ConnectionIndicator";
import LocaleSwitcher from "./LocaleSwitcher";
import { useT } from "../../i18n";
import { useAuth } from "../../auth/AuthContext";
import { Link } from "react-router-dom";
import { FROZEN_BRAND_NAME, FROZEN_TAGLINE } from "../../contracts/frozenShell";

interface AppHeaderProps {
  isWsConnected?: boolean;
}

export default function AppHeader({ isWsConnected = false }: AppHeaderProps) {
  const t = useT();
  const { token } = theme.useToken();
  const { mode, principal, logout } = useAuth();

  return (
    <Layout.Header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 24px",
        borderBottom: `1px solid ${token.colorBorder}`,
        height: 56,
      }}
    >
      <Link
        to="/control"
        aria-label="天枢中枢总览"
        style={{ display: "flex", alignItems: "center", gap: 10, textDecoration: "none" }}
      >
        {/* 品牌标:TS×轨道(与 README/favicon 同一枚 logo) */}
        <img
          src="/brand.png"
          alt=""
          aria-hidden
          width={26}
          height={26}
          style={{ display: "block", borderRadius: 6, flex: "none" }}
        />
        <span
          style={{
            color: token.colorText,
            fontFamily: "'Noto Serif SC', serif",
            fontWeight: 700,
            fontSize: 18,
            letterSpacing: 2,
            lineHeight: 1,
          }}
        >
          {FROZEN_BRAND_NAME}
        </span>
      </Link>
      <div
        style={{
          flex: 1,
          textAlign: "center",
          color: token.colorTextSecondary,
          fontFamily: "'Noto Serif SC', serif",
          fontSize: 12.5,
          letterSpacing: 2,
        }}
      >
        {FROZEN_TAGLINE}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <LocaleSwitcher />
        {principal ? (
          <span
            title={`${t("auth.currentUser")}: ${principal.id}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              color: token.colorTextSecondary,
              fontSize: 12,
              whiteSpace: "nowrap",
            }}
          >
            <UserOutlined />
            {principal.display_name}
          </span>
        ) : null}
        {mode === "secure-remote" ? (
          <Tooltip title={t("auth.logout")}>
            <Button
              type="text"
              size="small"
              icon={<LogoutOutlined />}
              aria-label={t("auth.logout")}
              onClick={() => void logout()}
            />
          </Tooltip>
        ) : null}
        <ConnectionIndicator isConnected={isWsConnected} />
        <HealthDot />
      </div>
    </Layout.Header>
  );
}
