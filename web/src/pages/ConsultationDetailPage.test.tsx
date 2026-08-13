// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConsultationResponse } from "../api/types";

const mocks = vi.hoisted(() => ({
  useConsultation: vi.fn(),
  useConsultationLiveUpdates: vi.fn(),
}));

vi.mock("../hooks/useConsultation", () => ({
  useConsultation: mocks.useConsultation,
  useConsultations: vi.fn(),
  useCreateConsultation: vi.fn(),
  useConsultationLiveUpdates: mocks.useConsultationLiveUpdates,
}));

import ConsultationDetailPage from "./ConsultationDetailPage";

function consultation(overrides: Partial<ConsultationResponse> = {}): ConsultationResponse {
  return {
    id: "consultation-1",
    status: "completed",
    request: { topic: "如何在 AI 时代构建个人竞争力?", persona_ids: ["zjz", "wym"] },
    opinions: [],
    synthesis: null,
    decision: null,
    synthesizer_persona_id: null,
    synthesizer_name: null,
    synthesizer_department: null,
    error: null,
    created_at: "2026-08-13T11:16:59.000Z",
    completed_at: "2026-08-13T11:18:00.000Z",
    ...overrides,
  } as ConsultationResponse;
}

function opinion(overrides = {}) {
  return {
    persona_id: "zjz",
    persona_name: "张居正",
    department: "neige",
    opinion: "吾乃张居正。夫AI者，术也非道也。\n第二，决策权不可外包。",
    stance: "conditional",
    conditions: ["严禁将核心决策权外包"],
    key_points: [],
    is_censor: true,
    ...overrides,
  };
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={["/consultation/consultation-1"]}>
      <Routes>
        <Route path="/consultation" element={<div>列表页 stub</div>} />
        <Route path="/consultation/:consultationId" element={<ConsultationDetailPage />} />
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
  mocks.useConsultation.mockReturnValue({
    data: consultation(),
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ConsultationDetailPage", () => {
  it("loads the consultation named by the URL", () => {
    renderDetail();
    expect(mocks.useConsultation).toHaveBeenCalledWith("consultation-1");
  });

  it("renders the full multi-line opinion body", () => {
    mocks.useConsultation.mockReturnValue({
      data: consultation({ opinions: [opinion()] as never }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDetail();

    expect(screen.getByText(/吾乃张居正/)).toBeInTheDocument();
    // 后续行不得丢失（issue #54 的解析缺陷在 UI 上的表现）
    expect(screen.getByText(/决策权不可外包/)).toBeInTheDocument();
    expect(screen.getByText("严禁将核心决策权外包")).toBeInTheDocument();
  });

  it("attributes synthesis and decision to the acting synthesizer", () => {
    mocks.useConsultation.mockReturnValue({
      data: consultation({
        synthesis: "核心共识……",
        decision: "三核一闭环……",
        synthesizer_persona_id: "zjz",
        synthesizer_name: "张居正",
        synthesizer_department: "neige",
      }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDetail();

    expect(screen.getAllByText("张居正 (neige)")).toHaveLength(2); // 综合意见 + 决策各一处署名
  });

  it("falls back to the chief counselor label when no synthesizer was chosen", () => {
    mocks.useConsultation.mockReturnValue({
      data: consultation({ synthesis: "核心共识……" }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDetail();

    expect(screen.getByText("首席顾问")).toBeInTheDocument();
  });

  it("shows a synthesizing state once every opinion has arrived", () => {
    mocks.useConsultation.mockReturnValue({
      data: consultation({
        status: "running",
        completed_at: null,
        opinions: [opinion(), opinion({ persona_id: "wym", persona_name: "王阳明" })] as never,
      }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDetail();

    // 意见收齐后不该继续显示停在 100% 的「廷议进行中」
    expect(screen.getByText("首辅汇总中...")).toBeInTheDocument();
    expect(screen.queryByText("廷议进行中...")).not.toBeInTheDocument();
  });

  it("keeps showing progress while opinions are still arriving", () => {
    mocks.useConsultation.mockReturnValue({
      data: consultation({
        status: "running",
        completed_at: null,
        opinions: [opinion()] as never,
      }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDetail();

    expect(screen.getByText("廷议进行中...")).toBeInTheDocument();
    expect(screen.getByText("已奏对 1/2")).toBeInTheDocument();
  });

  it("surfaces the failure reason instead of a generic message", () => {
    mocks.useConsultation.mockReturnValue({
      data: consultation({ status: "failed", error: "张三: timeout after 180s" }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDetail();

    expect(screen.getByText("张三: timeout after 180s")).toBeInTheDocument();
  });

  it("shows an empty state instead of a blank page when no opinion arrived", () => {
    renderDetail();
    expect(screen.getByText("本次廷议无人奏对")).toBeInTheDocument();
  });
});
