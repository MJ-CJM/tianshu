import { Segmented } from "antd";
import { useLocale, type Locale } from "../../hooks/useLocale";
import { useT } from "../../i18n";

export default function LocaleSwitcher() {
  const { locale, setLocale } = useLocale();
  const t = useT();
  return (
    <Segmented
      size="small"
      value={locale}
      onChange={(v) => setLocale(v as Locale)}
      options={[
        { label: t("locale.zh-classic"), value: "zh-classic" },
        { label: t("locale.zh-modern"), value: "zh-modern" },
        { label: t("locale.en"), value: "en" },
      ]}
    />
  );
}
