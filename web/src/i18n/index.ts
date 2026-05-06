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

export type TFunction = (key: string, fallback?: string) => string;

export function useT(): TFunction {
  const locale = useLocaleMode();
  return (key: string, fallback?: string): string => {
    const value = lookup(DICT[locale], key);
    if (value !== undefined) return value;
    // Fallback to zh-classic to ensure UI never shows raw keys
    if (locale !== "zh-classic") {
      const zhValue = lookup(DICT["zh-classic"], key);
      if (zhValue !== undefined) return zhValue;
    }
    return fallback ?? key;
  };
}
