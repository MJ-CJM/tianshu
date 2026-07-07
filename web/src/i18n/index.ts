import { useLocaleMode, type Locale } from "../hooks/useLocale";
import zhClassic from "./locales/zh-classic.json";
import zhModern from "./locales/zh-modern.json";
import en from "./locales/en.json";

type Dict = Record<string, unknown>;

const DICT: Record<Locale, Dict> = {
  "zh-classic": zhClassic as Dict,
  "zh-modern": zhModern as Dict,
  en: en as Dict,
};

function lookup(dict: Dict, key: string): string | undefined {
  const parts = key.split(".");
  let cur: unknown = dict;
  for (const part of parts) {
    if (cur && typeof cur === "object" && part in (cur as Dict)) {
      cur = (cur as Dict)[part];
    } else {
      return undefined;
    }
  }
  return typeof cur === "string" ? cur : undefined;
}

function interpolate(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    name in vars ? String(vars[name]) : match
  );
}

export type TFunction = (
  key: string,
  vars?: Record<string, string | number>,
) => string;

export function useT(): TFunction {
  const locale = useLocaleMode();
  return (key: string, vars?: Record<string, string | number>): string => {
    let value = lookup(DICT[locale], key);
    // Fallback to zh-classic (anchor language) so UI never shows raw keys
    if (value === undefined && locale !== "zh-classic") {
      value = lookup(DICT["zh-classic"], key);
    }
    if (value === undefined) value = key;
    return vars ? interpolate(value, vars) : value;
  };
}
