// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConsultationResponse, ConsultationRound, PersonaOpinion } from "../api/types";

const mocks = vi.hoisted(() => ({
  useConsultation: vi.fn(),
  useConsultationLiveUpdates: vi.fn(),
  useAppendRound: vi.fn(),
  useSetVerdict: vi.fn(),
  useSynthesizeRound: vi.fn(),
  usePersonas: vi.fn(),
  appendRound: vi.fn(),
  setVerdict: vi.fn(),
  synthesizeRound: vi.fn(),
}));

vi.mock("../hooks/useConsultation", () => ({
  useConsultation: mocks.useConsultation,
  useConsultations: vi.fn(),
  useCreateConsultation: vi.fn(),
  useConsultationLiveUpdates: mocks.useConsultationLiveUpdates,
  useAppendRound: mocks.useAppendRound,
  useSetVerdict: mocks.useSetVerdict,
  useSynthesizeRound: mocks.useSynthesizeRound,
}));
vi.mock("../hooks/usePersonas", () => ({ usePersonas: mocks.usePersonas }));

import ConsultationDetailPage from "./ConsultationDetailPage";

function opinion(overrides: Partial<PersonaOpinion> = {}): PersonaOpinion {
  return {
    persona_id: "zjz",
    persona_name: "张居正",
    department: "neige",
    opinion: "吾乃张居正。夫AI者，术也非道也。\n第二，决策权不可外包。",
    stance: "conditional",
    conditions: ["严禁将核心决策权外包"],
    key_points: [],
    is_censor: true,
    tool_calls: [],
    ...overrides,
  };
}

function round(overrides: Partial<ConsultationRound> = {}): ConsultationRound {
  return {
    id: "round-0",
    consultation_id: "consultation-1",
    round_index: 0,
    prompt: "如何评价秦始皇?",
    participant_ids: ["zjz", "smg"],
    status: "completed",
    opinions: [],
    synthesis: null,
    proposal: null,
    synthesizer_persona_id: null,
    synthesizer_name: null,
    synthesizer_department: null,
    error: null,
    created_at: "2026-08-13T11:16:59.000Z",
    completed_at: "2026-08-13T11:18:00.000Z",
    ...overrides,
  };
}

function consultation(overrides: Partial<ConsultationResponse> = {}): ConsultationResponse {
  return {
    id: "consultation-1",
    status: "completed",
    request: { topic: "如何评价秦始皇?", persona_ids: ["zjz", "smg"] },
    rounds: [round()],
    verdict: null,
    verdict_at: null,
    error: null,
    created_at: "2026-08-13T11:16:59.000Z",
    completed_at: "2026-08-13T11:18:00.000Z",
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

function mockConsultation(data: ConsultationResponse) {
  mocks.useConsultation.mockReturnValue({
    data,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
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
  mockConsultation(consultation());
  mocks.useAppendRound.mockReturnValue({
    mutate: mocks.appendRound,
    isPending: false,
    error: null,
  });
  mocks.useSetVerdict.mockReturnValue({
    mutate: mocks.setVerdict,
    isPending: false,
    error: null,
  });
  mocks.useSynthesizeRound.mockReturnValue({
    mutate: mocks.synthesizeRound,
    isPending: false,
    error: null,
  });
  mocks.usePersonas.mockReturnValue({
    data: [
      { id: "zjz", name: "张居正", department: "neige" },
      { id: "smg", name: "司马光", department: "wenyuan" },
      { id: "wym", name: "王阳明", department: "neige" },
    ],
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
    mockConsultation(consultation({ rounds: [round({ opinions: [opinion()] })] }));

    renderDetail();

    expect(screen.getByText(/吾乃张居正/)).toBeInTheDocument();
    expect(screen.getByText(/决策权不可外包/)).toBeInTheDocument();
    expect(screen.getByText("严禁将核心决策权外包")).toBeInTheDocument();
  });

  it("lays every round out on the timeline", () => {
    mockConsultation(
      consultation({
        rounds: [
          round({ opinions: [opinion()] }),
          round({
            id: "round-1",
            round_index: 1,
            prompt: "展开说说郡县制",
            participant_ids: ["smg"],
            opinions: [opinion({ persona_id: "smg", persona_name: "司马光", is_censor: false })],
          }),
        ],
      }),
    );

    renderDetail();

    expect(screen.getByText("第 1 轮")).toBeInTheDocument();
    expect(screen.getByText("第 2 轮")).toBeInTheDocument();
    expect(screen.getByText(/展开说说郡县制/)).toBeInTheDocument();
  });

  it("labels the LLM output as advisory proposal, attributed to the synthesizer", () => {
    mockConsultation(
      consultation({
        rounds: [
          round({
            synthesis: "核心共识……",
            proposal: "臣等拟：确立功过二分……",
            synthesizer_persona_id: "zjz",
            synthesizer_name: "张居正",
            synthesizer_department: "neige",
          }),
        ],
      }),
    );

    renderDetail();

    expect(screen.getByText("票拟（内阁建议，仅供参考）")).toBeInTheDocument();
    expect(screen.getAllByText("张居正 (neige)")).toHaveLength(2); // 综合意见 + 票拟
  });

  it("falls back to the chief counselor label when nobody was named", () => {
    mockConsultation(consultation({ rounds: [round({ synthesis: "核心共识……" })] }));
    renderDetail();
    expect(screen.getByText("首席顾问")).toBeInTheDocument();
  });

  it("offers a verdict box while no verdict has been recorded", async () => {
    const user = userEvent.setup();
    renderDetail();

    expect(screen.getByText("待裁决")).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("写下你的最终决定…"), "准奏，但须季度复核。");
    await user.click(screen.getByRole("button", { name: "落裁决" }));

    expect(mocks.setVerdict).toHaveBeenCalledWith("准奏，但须季度复核。", expect.anything());
  });

  it("shows the recorded verdict instead of the input once set", () => {
    mockConsultation(
      consultation({ verdict: "准奏，但须季度复核。", verdict_at: "2026-08-13T12:00:00.000Z" }),
    );

    renderDetail();

    expect(screen.getByText(/准奏，但须季度复核。/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("写下你的最终决定…")).not.toBeInTheDocument();
  });

  it("sends a follow-up round naming only the mentioned officials", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByText("司马光 (wenyuan)"));
    await user.type(screen.getByPlaceholderText("接着问下去…"), "展开说说郡县制");
    await user.click(screen.getByRole("button", { name: "奏" }));

    expect(mocks.appendRound).toHaveBeenCalledWith(
      { prompt: "展开说说郡县制", participant_ids: ["smg"] },
      expect.anything(),
    );
  });

  it("sends an unnamed follow-up so everyone answers", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.type(screen.getByPlaceholderText("接着问下去…"), "再议");
    await user.click(screen.getByRole("button", { name: "奏" }));

    expect(mocks.appendRound).toHaveBeenCalledWith(
      { prompt: "再议", participant_ids: [] },
      expect.anything(),
    );
  });

  it("blocks follow-ups while a round is still running", () => {
    mockConsultation(
      consultation({
        status: "running",
        rounds: [round({ status: "running", opinions: [opinion()] })],
      }),
    );

    renderDetail();

    expect(screen.getByRole("button", { name: "奏" })).toBeDisabled();
    expect(screen.getByText("本轮尚未议毕，暂不可追问")).toBeInTheDocument();
  });

  it("only offers roster members as mention targets", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("combobox"));

    expect(await screen.findByText("司马光 (wenyuan)")).toBeInTheDocument();
    // 王阳明不在本场廷议名单里，不该出现在点名列表
    expect(screen.queryByText("王阳明 (neige)")).not.toBeInTheDocument();
  });

  it("shows a synthesizing state once every opinion has arrived", () => {
    mockConsultation(
      consultation({
        status: "running",
        rounds: [
          round({
            status: "running",
            completed_at: null,
            opinions: [opinion(), opinion({ persona_id: "smg", persona_name: "司马光" })],
          }),
        ],
      }),
    );

    renderDetail();

    expect(screen.getByText("首辅汇总中...")).toBeInTheDocument();
    expect(screen.queryByText("廷议进行中...")).not.toBeInTheDocument();
  });

  it("keeps showing progress while opinions are still arriving", () => {
    mockConsultation(
      consultation({
        status: "running",
        rounds: [round({ status: "running", completed_at: null, opinions: [opinion()] })],
      }),
    );

    renderDetail();

    expect(screen.getByText("廷议进行中...")).toBeInTheDocument();
    expect(screen.getByText("已奏对 1/2")).toBeInTheDocument();
  });

  it("surfaces the failure reason instead of a generic message", () => {
    mockConsultation(
      consultation({
        status: "failed",
        rounds: [round({ status: "failed", error: "张三: timeout after 180s" })],
      }),
    );

    renderDetail();

    expect(screen.getByText("张三: timeout after 180s")).toBeInTheDocument();
  });

  it("shows an empty state instead of a blank round when no opinion arrived", () => {
    renderDetail();
    expect(screen.getByText("本次廷议无人奏对")).toBeInTheDocument();
  });

  it("offers on-demand synthesis for a round that has none", async () => {
    const user = userEvent.setup();
    mockConsultation(
      consultation({ rounds: [round({ id: "round-1", round_index: 1, opinions: [opinion()] })] }),
    );

    renderDetail();

    await user.click(screen.getByRole("button", { name: "请首辅票拟" }));

    expect(mocks.synthesizeRound).toHaveBeenCalledWith("round-1");
  });

  it("hides the synthesis button once the round already has one", () => {
    mockConsultation(
      consultation({
        rounds: [round({ opinions: [opinion()], synthesis: "已有综述", proposal: "已有票拟" })],
      }),
    );

    renderDetail();

    expect(screen.queryByRole("button", { name: "请首辅票拟" })).not.toBeInTheDocument();
  });

  it("hides the synthesis button when the round produced no opinion", () => {
    mockConsultation(consultation({ rounds: [round({ opinions: [] })] }));

    renderDetail();

    expect(screen.queryByRole("button", { name: "请首辅票拟" })).not.toBeInTheDocument();
  });

  it("names who a follow-up round was addressed to", () => {
    mockConsultation(
      consultation({
        rounds: [
          round(),
          round({
            id: "round-1",
            round_index: 1,
            prompt: "能联网查下吗?",
            participant_ids: ["wym"],
            opinions: [],
          }),
        ],
      }),
    );

    renderDetail();

    // 靠「谁回答了」反推不可靠：本轮尚无意见，点名对象仍须可见
    expect(screen.getByText("@王阳明")).toBeInTheDocument();
  });

  it("marks a round as open to everyone when nobody was named", () => {
    mockConsultation(
      consultation({
        rounds: [round({ participant_ids: ["zjz", "smg"] })], // 与全体名单一致
      }),
    );

    renderDetail();

    expect(screen.getByText("百官皆可")).toBeInTheDocument();
    expect(screen.queryByText("@张居正")).not.toBeInTheDocument();
  });

  it("falls back to the raw id when the official is no longer on file", () => {
    mockConsultation(
      consultation({
        rounds: [round({ participant_ids: ["ghost"], opinions: [] })],
      }),
    );

    renderDetail();

    expect(screen.getByText("@ghost")).toBeInTheDocument();
  });

  it("surfaces the research trail behind an opinion", async () => {
    const user = userEvent.setup();
    mockConsultation(
      consultation({
        rounds: [
          round({
            opinions: [
              opinion({
                tool_calls: [
                  {
                    tool: "web_search",
                    args_preview: '{"query": "deepseek harness"}',
                    result_preview: "找到 3 条结果",
                    is_error: false,
                  },
                ],
              }),
            ],
          }),
        ],
      }),
    );

    renderDetail();

    // 徽标先告诉读者「这段意见查过资料」
    expect(screen.getByText("已查证 1")).toBeInTheDocument();
    // 展开后能看到查了什么
    await user.click(screen.getByText("查证痕迹（1 次）"));
    expect(screen.getByText("web_search")).toBeInTheDocument();
    expect(screen.getByText(/deepseek harness/)).toBeInTheDocument();
  });

  it("shows no trail affordance when the official did not look anything up", () => {
    mockConsultation(
      consultation({ rounds: [round({ opinions: [opinion({ tool_calls: [] })] })] }),
    );

    renderDetail();

    expect(screen.queryByText(/已查证/)).not.toBeInTheDocument();
    expect(screen.queryByText(/查证痕迹/)).not.toBeInTheDocument();
  });
});
