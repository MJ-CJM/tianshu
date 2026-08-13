// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  usePersonas: vi.fn(),
  useConsultations: vi.fn(),
  useCreateConsultation: vi.fn(),
  useConsultationLiveUpdates: vi.fn(),
  create: vi.fn(),
}));

vi.mock("../hooks/usePersonas", () => ({ usePersonas: mocks.usePersonas }));
vi.mock("../hooks/useConsultation", () => ({
  useConsultation: vi.fn(),
  useConsultations: mocks.useConsultations,
  useCreateConsultation: mocks.useCreateConsultation,
  useConsultationLiveUpdates: mocks.useConsultationLiveUpdates,
}));

import ConsultationPage from "./ConsultationPage";

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/consultation"]}>
      <Routes>
        <Route path="/consultation" element={<ConsultationPage />} />
        <Route path="/consultation/:consultationId" element={<div>详情页 stub</div>} />
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
    data: [{ id: "persona-1", name: "张三", department: "bingbu" }],
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

    renderPage();

    expect(screen.getByRole("alert")).toHaveTextContent("create consultation unavailable");
  });

  it("navigates to the standalone detail page after submitting", async () => {
    const user = userEvent.setup();
    mocks.create.mockImplementation((_body, options) => {
      options.onSuccess({ data: { id: "consultation-1" } });
    });
    renderPage();

    await user.type(screen.getByPlaceholderText("请输入廷议议题"), "边务议题");
    await user.click(screen.getAllByRole("combobox")[0]!); // 参与百官
    await user.click(await screen.findByText("张三 (bingbu)"));
    await user.click(screen.getByRole("button", { name: "发起廷议" }));

    expect(screen.getByText("详情页 stub")).toBeInTheDocument();
  });

  it("passes the chosen synthesizer through to the request", async () => {
    const user = userEvent.setup();
    mocks.create.mockImplementation((_body, options) => {
      options.onSuccess({ data: { id: "consultation-1" } });
    });
    renderPage();

    await user.type(screen.getByPlaceholderText("请输入廷议议题"), "边务议题");
    await user.click(screen.getAllByRole("combobox")[0]!); // 参与百官
    await user.click(await screen.findByText("张三 (bingbu)"));
    await user.click(screen.getAllByRole("combobox")[2]!); // 首席顾问
    const options = await screen.findAllByText("张三 (bingbu)");
    await user.click(options[options.length - 1]!);
    await user.click(screen.getByRole("button", { name: "发起廷议" }));

    expect(mocks.create).toHaveBeenCalledWith(
      expect.objectContaining({ synthesizer_persona_id: "persona-1" }),
      expect.anything(),
    );
  });

  it("lists past consultations and opens them on click", async () => {
    const user = userEvent.setup();
    mocks.useConsultations.mockReturnValue({
      data: [
        {
          id: "consultation-9",
          status: "completed",
          request: { topic: "去年的旧议题", persona_ids: [] },
          rounds: [],
          verdict: null,
          verdict_at: null,
          error: null,
          created_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
        },
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderPage();

    await user.click(screen.getByText("去年的旧议题"));

    expect(screen.getByText("详情页 stub")).toBeInTheDocument();
  });
});

describe("ConsultationPage 角色任命", () => {
  it("passes the named censors through to the request", async () => {
    const user = userEvent.setup();
    mocks.create.mockImplementation((_body, options) => {
      options.onSuccess({ data: { id: "consultation-1" } });
    });
    renderPage();

    await user.type(screen.getByPlaceholderText("请输入廷议议题"), "边务议题");
    await user.click(screen.getAllByRole("combobox")[0]!); // 参与百官
    await user.click(await screen.findByText("张三 (bingbu)"));
    await user.click(screen.getAllByRole("combobox")[1]!); // 言官
    const options = await screen.findAllByText("张三 (bingbu)");
    await user.click(options[options.length - 1]!);
    await user.click(screen.getByRole("button", { name: "发起廷议" }));

    expect(mocks.create).toHaveBeenCalledWith(
      expect.objectContaining({ censor_persona_ids: ["persona-1"] }),
      expect.anything(),
    );
  });

  it("omits censors when nobody was named", async () => {
    const user = userEvent.setup();
    mocks.create.mockImplementation((_body, options) => {
      options.onSuccess({ data: { id: "consultation-1" } });
    });
    renderPage();

    await user.type(screen.getByPlaceholderText("请输入廷议议题"), "边务议题");
    await user.click(screen.getAllByRole("combobox")[0]!);
    await user.click(await screen.findByText("张三 (bingbu)"));
    await user.click(screen.getByRole("button", { name: "发起廷议" }));

    const body = mocks.create.mock.calls[0]![0];
    expect(body.censor_persona_ids).toBeUndefined();
  });

  it("only offers already-chosen participants as censors", async () => {
    const user = userEvent.setup();
    mocks.usePersonas.mockReturnValue({
      data: [
        { id: "persona-1", name: "张三", department: "bingbu" },
        { id: "persona-2", name: "李四", department: "hubu" },
      ],
      error: null,
      refetch: vi.fn(),
    });
    renderPage();

    await user.click(screen.getAllByRole("combobox")[0]!); // 参与百官
    await user.click(await screen.findByText("张三 (bingbu)"));
    await user.click(screen.getAllByRole("combobox")[1]!); // 言官

    // 只看当前展开的那个下拉——已关闭的下拉节点仍留在 DOM 里，全局查会误命中
    const visible = document.querySelectorAll(
      ".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-content",
    );
    const labels = [...visible].map((el) => el.textContent);
    // 点名一个没上朝的人没有意义——李四未入选，不该出现在言官候选里
    expect(labels).toEqual(["张三 (bingbu)"]);
  });
});
