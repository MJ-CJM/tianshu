// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hookMocks = vi.hoisted(() => ({
  useSetCostBudget: vi.fn(),
}));

vi.mock("../../hooks/useCost", () => hookMocks);

import BudgetProgressBar from "./BudgetProgressBar";

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("BudgetProgressBar editing", () => {
  it("preserves the existing budget period when only the amount is edited", async () => {
    const mutate = vi.fn();
    hookMocks.useSetCostBudget.mockReturnValue({
      mutate,
      isPending: false,
    });
    const user = userEvent.setup();

    render(
      <BudgetProgressBar
        budget={{
          scope: "global",
          budget_cny: 20,
          spent_cny: 0,
          remaining_cny: 20,
          period: "daily",
          exceeded: false,
        }}
        loading={false}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: /修改预算|Edit budget/ }),
    );
    const amount = screen.getByRole("spinbutton");
    await user.clear(amount);
    await user.type(amount, "21");
    await user.click(screen.getByRole("button", { name: /保\s*存|Save/ }));

    expect(mutate).toHaveBeenCalledWith(
      { scope: "global", budgetCny: 21, period: "daily" },
      expect.any(Object),
    );
  });
});
