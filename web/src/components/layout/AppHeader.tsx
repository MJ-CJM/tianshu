import { Layout, theme } from "antd";
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
