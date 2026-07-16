import { Segmented } from "antd";
import { useLocale, type Locale } from "../../hooks/useLocale";
import { FROZEN_LOCALE_LABELS } from "../../contracts/frozenShell";

export default function LocaleSwitcher() {
  const { locale, setLocale } = useLocale();
  return (
    <Segmented
      size="small"
      value={locale}
      onChange={(v) => setLocale(v as Locale)}
      options={[
        { label: FROZEN_LOCALE_LABELS["zh-classic"], value: "zh-classic" },
        { label: FROZEN_LOCALE_LABELS["zh-modern"], value: "zh-modern" },
        { label: FROZEN_LOCALE_LABELS.en, value: "en" },
      ]}
    />
  );
}
