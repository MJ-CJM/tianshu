import {
  createContext,
  useContext,
  useCallback,
  useSyncExternalStore,
} from "react";
import { palettes } from "../theme/palette";

export type ThemeMode = "light" | "dark";

interface ThemeContextValue {
  mode: ThemeMode;
  toggleTheme: () => void;
}

const STORAGE_KEY = "tianshu-theme";

/** 从调色板生成 CSS 变量表(module.css 与内联样式的取色入口)。 */
function buildCssVars(mode: ThemeMode): Record<string, string> {
  const p = palettes[mode];
  return {
    "--ts-color-text": p.text,
    "--ts-color-text-secondary": p.textSecondary,
    "--ts-color-text-label": p.textTertiary,
    "--ts-color-bg-page": p.bgPage,
    "--ts-color-bg-subtle": p.bgSubtle,
    "--ts-color-bg-container": p.bgContainer,
    "--ts-color-bg-elevated": p.bgElevated,
    "--ts-color-bg-code": p.bgCode,
    "--ts-color-border": p.border,
    "--ts-color-border-hover": p.borderHover,
    "--ts-color-scrollbar": p.scrollbar,
    "--ts-color-scrollbar-hover": p.scrollbarHover,
    "--ts-color-accent": p.accent,
    "--ts-color-accent-hover": p.accentHover,
    "--ts-color-accent-soft": p.accentSoft,
    "--ts-color-accent-text-on": p.accentTextOn,
    "--ts-color-info": p.info,
    "--ts-color-success": p.success,
    "--ts-color-warning": p.warning,
    "--ts-color-error": p.error,
    "--ts-color-surface": p.surface,
    "--ts-color-surface-raised": p.surfaceRaised,
    "--ts-color-focus-ring": p.focusRing,
    "--ts-color-decision": p.decision,
    "--ts-color-blocked": p.blocked,
    "--ts-status-running": p.status.running,
    "--ts-status-completed": p.status.completed,
    "--ts-status-failed": p.status.failed,
    "--ts-status-submitted": p.status.submitted,
    "--ts-status-scheduled": p.status.scheduled,
    "--ts-status-planning": p.status.planning,
    "--ts-status-auditing": p.status.auditing,
    "--ts-status-needs-review": p.status.needs_review,
    "--ts-status-cancelled": p.status.cancelled,
  };
}

function getStoredMode(): ThemeMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    // localStorage unavailable
  }
  return "dark";
}

function applyCssVars(mode: ThemeMode) {
  const root = document.documentElement;
  root.dataset.theme = mode;
  for (const [key, value] of Object.entries(buildCssVars(mode))) {
    root.style.setProperty(key, value);
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
  mode: "dark",
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
