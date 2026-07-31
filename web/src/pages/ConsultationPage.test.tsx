// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  usePersonas: vi.fn(),
  useConsultation: vi.fn(),
  useCreateConsultation: vi.fn(),
  create: vi.fn(),
}));

vi.mock("../hooks/usePersonas", () => ({ usePersonas: mocks.usePersonas }));
vi.mock("../hooks/useConsultation", () => ({
  useConsultation: mocks.useConsultation,
  useCreateConsultation: mocks.useCreateConsultation,
}));

import ConsultationPage from "./ConsultationPage";

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
  mocks.usePersonas.mockReturnValue({
    data: [
      {
        id: "persona-1",
        name: "张三",
        department: "bingbu",
      },
    ],
    error: null,
    refetch: vi.fn(),
  });
  mocks.useConsultation.mockReturnValue({
    data: null,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
  mocks.useCreateConsultation.mockReturnValue({
    mutate: mocks.create,
    isPending: false,
    error: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ConsultationPage", () => {
  it("renders creation failures as a persistent visible error", () => {
    mocks.useCreateConsultation.mockReturnValue({
      mutate: mocks.create,
      isPending: false,
      error: new Error("create consultation unavailable"),
    });

    render(<ConsultationPage />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "create consultation unavailable",
    );
  });

  it("uses real buttons to switch between consultation rounds", async () => {
    const user = userEvent.setup();
    let sequence = 0;
    mocks.create.mockImplementation((_body, options) => {
      sequence += 1;
      options.onSuccess({ data: { id: `consultation-${sequence}` } });
    });
    render(<ConsultationPage />);

    await user.type(screen.getByPlaceholderText("请输入廷议议题"), "边务议题");
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByText("张三 (bingbu)"));
    const submit = screen.getByRole("button", { name: "发起廷议" });
    await user.click(submit);
    await user.click(submit);

    const firstRound = screen.getByRole("button", { name: "廷议 #1" });
    const secondRound = screen.getByRole("button", { name: "廷议 #2" });
    expect(secondRound).toHaveAttribute("aria-pressed", "true");

    await user.click(firstRound);

    expect(firstRound).toHaveAttribute("aria-pressed", "true");
    expect(secondRound).toHaveAttribute("aria-pressed", "false");
  });
});
