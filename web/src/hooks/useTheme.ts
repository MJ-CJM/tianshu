import { createContext, useContext, useCallback, useSyncExternalStore } from "react";

export type ThemeMode = "light" | "dark";

interface ThemeContextValue {
  mode: ThemeMode;
  toggleTheme: () => void;
}

const STORAGE_KEY = "tianshu-theme";

const CSS_VARS: Record<string, { light: string; dark: string }> = {
  "--ts-color-text": { light: "#1a1a1a", dark: "#e8e8e8" },
  "--ts-color-text-secondary": { light: "#8e8e8e", dark: "#999999" },
  "--ts-color-text-label": { light: "#666666", dark: "#aaaaaa" },
  "--ts-color-bg-page": { light: "#f8f8f7", dark: "#141414" },
  "--ts-color-bg-subtle": { light: "#f9f9f8", dark: "#1f1f1f" },
  "--ts-color-bg-container": { light: "#ffffff", dark: "#1f1f1f" },
  "--ts-color-bg-code": { light: "#f5f5f4", dark: "#1a1a1a" },
  "--ts-color-border": { light: "#e8e8e5", dark: "#303030" },
  "--ts-color-border-hover": { light: "#d8d8d5", dark: "#555555" },
  "--ts-color-scrollbar": { light: "#d0d0d0", dark: "#555555" },
  "--ts-color-scrollbar-hover": { light: "#b0b0b0", dark: "#777777" },
};

function getStoredMode(): ThemeMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    // localStorage unavailable
  }
  return "light";
}

function applyCssVars(mode: ThemeMode) {
  const root = document.documentElement;
  root.dataset.theme = mode;
  for (const [key, values] of Object.entries(CSS_VARS)) {
    root.style.setProperty(key, values[mode]);
  }
}

// Module-level state for useSyncExternalStore
let currentMode: ThemeMode = getStoredMode();
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): ThemeMode {
  return currentMode;
}

function setMode(mode: ThemeMode) {
  currentMode = mode;
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // localStorage unavailable
  }
  applyCssVars(mode);
  listeners.forEach((l) => l());
}

// Apply on module load
applyCssVars(currentMode);

export const ThemeContext = createContext<ThemeContextValue>({
  mode: "light",
  toggleTheme: () => {},
});

export function useThemeMode(): ThemeMode {
  return useSyncExternalStore(subscribe, getSnapshot);
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}

export function useThemeProvider(): ThemeContextValue {
  const mode = useThemeMode();

  const toggleTheme = useCallback(() => {
    setMode(mode === "light" ? "dark" : "light");
  }, [mode]);

  return { mode, toggleTheme };
}
