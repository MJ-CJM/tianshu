import { Segmented } from "antd";
import { useLocale, type Locale } from "../../hooks/useLocale";
import { FROZEN_LOCALE_LABELS } from "../../contracts/frozenShell";

const OPTIONS = [
  { label: FROZEN_LOCALE_LABELS["zh-classic"], value: "zh-classic" },
  { label: FROZEN_LOCALE_LABELS["zh-modern"], value: "zh-modern" },
  { label: FROZEN_LOCALE_LABELS.en, value: "en" },
] satisfies Array<{ label: string; value: Locale }>;

export default function LocaleSwitcher() {
  const { locale, setLocale } = useLocale();
  const selectLocale = (next: Locale) => {
    setLocale(next);
    requestAnimationFrame(() => {
      document
        .querySelector<HTMLInputElement>(
          `.ant-segmented-item-input[value="${next}"]`,
        )
        ?.focus({ preventScroll: true });
    });
  };
  return (
    <Segmented
      size="small"
      value={locale}
      onChange={(v) => selectLocale(v as Locale)}
      options={OPTIONS}
    />
  );
}
