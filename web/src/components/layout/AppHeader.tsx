import { Layout, theme } from "antd";
import SealMark from "../common/SealMark";
import HealthDot from "../common/HealthDot";
import ConnectionIndicator from "../common/ConnectionIndicator";
import LocaleSwitcher from "./LocaleSwitcher";
import { useT } from "../../i18n";
import { useLocaleMode } from "../../hooks/useLocale";

interface AppHeaderProps {
  isWsConnected?: boolean;
}

export default function AppHeader({ isWsConnected = false }: AppHeaderProps) {
  const t = useT();
  const locale = useLocaleMode();
  const { token } = theme.useToken();
  const brand = t("comp.appHeader.brand");
  const isLatinBrand = locale === "en";

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
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {/* 品牌印:朱砂方印「枢」(印面+印框+白文) */}
        <SealMark size={26} />
        <span
          style={{
            color: token.colorText,
            fontFamily: isLatinBrand ? "'Noto Serif', serif" : "'Noto Serif SC', serif",
            fontWeight: 700,
            fontSize: 18,
            letterSpacing: isLatinBrand ? 0.5 : 2,
            lineHeight: 1,
          }}
        >
          {brand}
        </span>
      </div>
      <div
        style={{
          flex: 1,
          textAlign: "center",
          color: token.colorTextSecondary,
          fontFamily: isLatinBrand ? "'Noto Serif', serif" : "'Noto Serif SC', serif",
          fontSize: 12.5,
          letterSpacing: 2,
        }}
      >
        {t("comp.appHeader.tagline")}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <LocaleSwitcher />
        <ConnectionIndicator isConnected={isWsConnected} />
        <HealthDot />
      </div>
    </Layout.Header>
  );
}
