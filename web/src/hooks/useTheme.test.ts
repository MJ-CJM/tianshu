// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, String(value));
    },
  };
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("style");
  vi.resetModules();
});

afterEach(() => vi.unstubAllGlobals());

describe("theme preference", () => {
  it("defaults to dark when no preference is stored", async () => {
    await import("./useTheme");

    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("preserves an explicit light preference across remounts", async () => {
    localStorage.setItem("tianshu-theme", "light");

    await import("./useTheme");
    expect(document.documentElement.dataset.theme).toBe("light");

    vi.resetModules();
    await import("./useTheme");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
