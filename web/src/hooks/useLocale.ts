import { createContext, useContext, useCallback, useSyncExternalStore } from "react";

export type Locale = "zh-classic" | "zh-modern" | "en";

interface LocaleContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
}

const STORAGE_KEY = "tianshu-locale";

function getStoredLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "zh-classic" || stored === "zh-modern" || stored === "en") {
      return stored;
    }
  } catch {
    // localStorage unavailable
  }
  return "zh-classic";
}

let currentLocale: Locale = getStoredLocale();
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): Locale {
  return currentLocale;
}

function persistLocale(locale: Locale) {
  currentLocale = locale;
  try {
    localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // localStorage unavailable
  }
  listeners.forEach((l) => l());
}

export const LocaleContext = createContext<LocaleContextValue>({
  locale: "zh-classic",
  setLocale: () => {},
});

export function useLocaleMode(): Locale {
  return useSyncExternalStore(subscribe, getSnapshot);
}

export function useLocale(): LocaleContextValue {
  return useContext(LocaleContext);
}

export function useLocaleProvider(): LocaleContextValue {
  const locale = useLocaleMode();
  const setLocale = useCallback((l: Locale) => {
    persistLocale(l);
  }, []);
  return { locale, setLocale };
}
