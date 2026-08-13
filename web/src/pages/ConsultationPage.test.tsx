// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  usePersonas: vi.fn(),
  useConsultation: vi.fn(),
  useConsultations: vi.fn(),
  useCreateConsultation: vi.fn(),
  useConsultationLiveUpdates: vi.fn(),
  create: vi.fn(),
}));

vi.mock("../hooks/usePersonas", () => ({ usePersonas: mocks.usePersonas }));
vi.mock("../hooks/useConsultation", () => ({
  useConsultation: mocks.useConsultation,
  useConsultations: mocks.useConsultations,
  useCreateConsultation: mocks.useCreateConsultation,
  useConsultationLiveUpdates: mocks.useConsultationLiveUpdates,
}));

import ConsultationPage from "./ConsultationPage";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/consultation" element={<ConsultationPage />} />
        <Route path="/consultation/:consultationId" element={<ConsultationPage />} />
      </Routes>
    </MemoryRouter>,
  );
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
  mocks.useConsultations.mockReturnValue({
    data: [],
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

    renderAt("/consultation");

    expect(screen.getByRole("alert")).toHaveTextContent(
      "create consultation unavailable",
    );
  });

  it("puts the new consultation id in the URL so a refresh can recover it", async () => {
    const user = userEvent.setup();
    mocks.create.mockImplementation((_body, options) => {
      options.onSuccess({ data: { id: "consultation-1" } });
    });
    renderAt("/consultation");

    await user.type(screen.getByPlaceholderText("请输入廷议议题"), "边务议题");
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByText("张三 (bingbu)"));
    await user.click(screen.getByRole("button", { name: "发起廷议" }));

    // 导航到带 id 的地址后，页面据 URL 参数拉取该场廷议
    expect(mocks.useConsultation).toHaveBeenLastCalledWith("consultation-1");
  });

  it("loads the consultation named by the URL on a cold render", () => {
    renderAt("/consultation/consultation-42");

    expect(mocks.useConsultation).toHaveBeenCalledWith("consultation-42");
  });

  it("shows an empty state instead of a blank page when no opinion arrived", () => {
    mocks.useConsultation.mockReturnValue({
      data: {
        id: "consultation-1",
        status: "completed",
        request: { topic: "议题", persona_ids: ["persona-1"] },
        opinions: [],
        synthesis: null,
        decision: null,
        error: null,
        created_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderAt("/consultation/consultation-1");

    expect(screen.getByText("本次廷议无人奏对")).toBeInTheDocument();
  });

  it("surfaces the failure reason instead of a generic message", () => {
    mocks.useConsultation.mockReturnValue({
      data: {
        id: "consultation-1",
        status: "failed",
        request: { topic: "议题", persona_ids: ["persona-1"] },
        opinions: [],
        synthesis: null,
        decision: null,
        error: "张三: timeout after 180s",
        created_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderAt("/consultation/consultation-1");

    expect(screen.getByText("张三: timeout after 180s")).toBeInTheDocument();
  });

  it("lists past consultations so they survive a refresh", async () => {
    const user = userEvent.setup();
    mocks.useConsultations.mockReturnValue({
      data: [
        {
          id: "consultation-9",
          status: "completed",
          request: { topic: "去年的旧议题", persona_ids: [] },
          opinions: [],
          synthesis: null,
          decision: null,
          error: null,
          created_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
        },
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderAt("/consultation");

    await user.click(screen.getByText("去年的旧议题"));

    expect(mocks.useConsultation).toHaveBeenLastCalledWith("consultation-9");
  });
});
