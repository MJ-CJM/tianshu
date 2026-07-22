// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  logout: vi.fn(async () => undefined),
  value: {
    status: "authenticated",
    mode: "secure-remote",
    principal: {
      id: "user:owner",
      kind: "human",
      display_name: "Owner",
      scopes: ["api", "admin"],
    },
  },
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ ...auth.value, login: vi.fn(), logout: auth.logout, retry: vi.fn() }),
}));
vi.mock("../common/HealthDot", () => ({ default: () => <span>health</span> }));
vi.mock("../common/ConnectionIndicator", () => ({ default: () => <span>connection</span> }));
vi.mock("./LocaleSwitcher", () => ({ default: () => <span>locale</span> }));

import AppHeader from "./AppHeader";

function renderHeader() {
  return render(
    <MemoryRouter>
      <AppHeader />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  auth.logout.mockClear();
  auth.value.mode = "secure-remote";
  auth.value.principal = {
    id: "user:owner",
    kind: "human",
    display_name: "Owner",
    scopes: ["api", "admin"],
  };
});

describe("application identity header", () => {
  it("shows the secure principal and provides an explicit logout action", () => {
    renderHeader();

    expect(screen.queryByText("Owner")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    expect(auth.logout).toHaveBeenCalledTimes(1);
  });

  it("shows trusted-local identity without a meaningless logout action", () => {
    auth.value.mode = "trusted-local";
    auth.value.principal = {
      id: "local:owner",
      kind: "local",
      display_name: "Local Owner",
      scopes: ["api"],
    };

    renderHeader();

    expect(screen.queryByText("Local Owner")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "退出登录" })).toBeNull();
  });
});
