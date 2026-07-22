import { useCallback, useState } from "react";

const STORAGE_KEY = "tianshu-sidebar-collapsed";

function getStoredState(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function useSidebarState() {
  const [collapsed, setCollapsedValue] = useState(getStoredState);
  const setCollapsed = useCallback((value: boolean) => {
    setCollapsedValue(value);
    try {
      localStorage.setItem(STORAGE_KEY, String(value));
    } catch {
      // localStorage unavailable
    }
  }, []);

  return { collapsed, setCollapsed };
}
