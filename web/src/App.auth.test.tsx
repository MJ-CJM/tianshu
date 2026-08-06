// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { Outlet } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./components/layout/AppLayout", () => ({
  default: () => (
    <div data-testid="app-layout">
      <Outlet />
    </div>
  ),
}));
vi.mock("./pages/RoyalStudyPage", () => ({
  default: () => <div>home-page</div>,
}));

import App from "./App";
import { resetAuthRefreshForTests } from "./api/authFetch";

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
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  vi.stubGlobal("localStorage", memoryStorage());
  vi.stubGlobal("sessionStorage", memoryStorage());
});

afterEach(() => {
  cleanup();
  resetAuthRefreshForTests();
  vi.unstubAllGlobals();
});

describe("application authentication boundary", () => {
  it("does not mount application routes before a secure session is established", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/auth/mode")) {
        return Response.json({ mode: "secure-remote", login_required: true });
      }
      return Response.json({}, { status: 401 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByLabelText("访问令牌")).not.toBeNull();
    expect(screen.queryByTestId("app-layout")).toBeNull();
  });
});
