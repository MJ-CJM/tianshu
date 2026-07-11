import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App.jsx";

const LEGACY_OFFICIALS_LABEL = ["百官", "图"].join("");
const FORBIDDEN_CONTROL_COPY = new RegExp(
  [
    ["批", "红"].join(""),
    ["朱", "批"].join(""),
    ["审", "批"].join(""),
    ["待", "批"].join(""),
    ["决", "策"].join(""),
    ["系统", "可信"].join(""),
    ["置信", "度"].join(""),
  ].join("|"),
);

afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute("data-theme");
});

describe("Tianshu Agent OS approval prototype", () => {
  it("keeps the production department map", () => {
    render(<App />);

    const sidebar = screen.getByRole("complementary");
    const navigation = within(sidebar).getByRole("navigation", { name: "天枢部门" });

    expect([...navigation.querySelectorAll(".nav-group-title")].map((node) => node.textContent)).toEqual([
      "敕令",
      "政要",
      "百官",
      "外朝",
    ]);
    expect(within(navigation).getAllByRole("button").map((button) => button.getAttribute("aria-label"))).toEqual([
      "中枢总览",
      "御书房",
      "文书房",
      "内阁",
      "廷议",
      "都察院",
      "权印司",
      "百官阁",
      "文渊阁",
      "位面",
      "考成",
      "藏兵阁",
      "鸿胪寺",
      "通政司",
      "户部账房",
    ]);
    expect(within(navigation).queryByRole("button", { name: LEGACY_OFFICIALS_LABEL })).not.toBeInTheDocument();
  });

  it("opens the governance detail from the control center", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "打开敕令详情" }));
    expect(screen.getByRole("heading", { name: /开源发布安全加固/ })).toBeInTheDocument();
    expect(screen.getByText("执行者不能自证完成")).toBeInTheDocument();
  });

  it("resets the content scroll position when switching approval screens", async () => {
    const user = userEvent.setup();
    render(<App />);
    const main = screen.getByRole("main");
    main.scrollTop = 59;

    await user.click(screen.getByRole("button", { name: "位面" }));

    expect(screen.getByRole("main").scrollTop).toBe(0);
  });

  it("preserves the current logo and the original header controls", async () => {
    const user = userEvent.setup();
    render(<App />);
    const header = screen.getByRole("banner");

    expect(within(header).getByRole("img", { name: "天枢 Logo" })).toHaveAttribute("src", expect.stringContaining("brand"));
    expect(within(header).getByText("成功只有一个——按照自己的方式，去度过人生。")).toBeInTheDocument();
    for (const label of ["彩蛋", "通用", "English"]) {
      expect(within(header).getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(within(header).getByText("实时")).toBeInTheDocument();
    expect(within(header).getByText("通政")).toBeInTheDocument();
    expect(within(header).getByLabelText("系统状态")).toHaveTextContent(/^彩蛋通用English实时通政$/);

    await user.click(within(header).getByRole("button", { name: "English" }));
    expect(within(header).getByRole("button", { name: "English" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps theme and collapse controls at the bottom of the sidebar", async () => {
    const user = userEvent.setup();
    render(<App />);
    const sidebar = screen.getByRole("complementary");

    expect(within(sidebar).queryByText("3 个执行器可用")).not.toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(within(sidebar).getByRole("button", { name: "浅色模式" })).toBeInTheDocument();

    await user.click(within(sidebar).getByRole("button", { name: "浅色模式" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(within(sidebar).getByRole("button", { name: "深色模式" })).toBeInTheDocument();

    await user.click(within(sidebar).getByRole("button", { name: "收起侧栏" }));
    expect(sidebar).toHaveClass("is-collapsed");
    expect(within(sidebar).getByRole("button", { name: "展开侧栏" })).toBeInTheDocument();
  });

  it("describes the current prototype round as acceptance-only", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "文书房" }));
    expect(screen.getByText("该部门保留在正式信息架构中，本轮只验收中枢、敕令详情与演化中心。")).toBeInTheDocument();
  });

  it("exposes measured evidence instead of unverifiable trust or confidence claims", () => {
    render(<App />);
    const main = screen.getByRole("main");

    expect(main).not.toHaveTextContent(FORBIDDEN_CONTROL_COPY);
    expect(within(main).getByText("证据完整率")).toBeInTheDocument();
    expect(within(main).getByText("97 / 100")).toBeInTheDocument();
    expect(within(main).getByText("过去 24h · 缺 3 项")).toBeInTheDocument();
    expect(within(main).getByText("4 次审计 · 3 次复用成功 · 1 次失败待复盘")).toBeInTheDocument();
  });

  it("requires a reason for a high-risk decision", async () => {
    const user = userEvent.setup();
    render(<App initialScreen="edict" />);
    const decisionPanel = screen.getByRole("region", { name: "当前需要裁决" });

    expect(screen.getByText("执行者不能自证完成")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "批准执行" }));
    expect(screen.getByRole("status")).toHaveTextContent("请填写裁决理由");
    expect(screen.queryByText("已批准执行")).not.toBeInTheDocument();

    const reasonField = within(decisionPanel).getByRole("textbox", { name: "裁决理由" });
    await user.type(reasonField, "  仅允许在净化环境中运行既定测试  ");
    await user.click(screen.getByRole("button", { name: "批准执行" }));
    expect(screen.getByRole("status")).toHaveTextContent("已批准");
    expect(screen.getByText(/裁决依据：仅允许在净化环境中运行既定测试/)).toBeInTheDocument();

    await user.type(reasonField, "（不得追加）");
    expect(reasonField).toHaveValue("仅允许在净化环境中运行既定测试");
    expect(screen.getByRole("status")).toHaveTextContent("裁决依据：仅允许在净化环境中运行既定测试");
    expect(reasonField).toBeDisabled();
    for (const buttonName of ["驳回", "修改后批准", "批准执行"]) {
      expect(within(decisionPanel).getByRole("button", { name: buttonName })).toBeDisabled();
    }
  });

  it("blocks promotion until every mandatory gate passes", async () => {
    const user = userEvent.setup();
    render(<App initialScreen="evolution" />);

    expect(screen.getByText("先回归评测，再晋升")).toBeInTheDocument();
    expect(screen.getAllByText("18 / 50").length).toBeGreaterThan(0);
    expect(screen.getByText("Canary 样本未达强制门槛")).toBeInTheDocument();
    expect(screen.getByText(/达到 50 个样本后才开放人工晋升/)).toBeInTheDocument();
    const promoteButton = screen.getByRole("button", { name: "批准晋升" });
    expect(promoteButton).toBeDisabled();
    await user.click(promoteButton);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("can restore an interactive decision screen to its initial local state", async () => {
    const user = userEvent.setup();
    render(<App initialScreen="edict" />);

    await user.type(screen.getByRole("textbox", { name: "裁决理由" }), "仅运行既定测试");
    await user.click(screen.getByRole("button", { name: "批准执行" }));
    expect(screen.getByRole("status")).toHaveTextContent("已批准执行");

    await user.click(screen.getByRole("button", { name: "恢复初始状态" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "裁决理由" })).toHaveValue("");
    expect(screen.getByRole("textbox", { name: "裁决理由" })).toBeEnabled();
    for (const buttonName of ["驳回", "修改后批准", "批准执行"]) {
      expect(screen.getByRole("button", { name: buttonName })).toBeEnabled();
    }
  });

  it("keeps a completed decision locked across navigation until explicit reset", async () => {
    const user = userEvent.setup();
    render(<App initialScreen="edict" />);

    await user.type(screen.getByRole("textbox", { name: "裁决理由" }), "仅运行既定测试");
    await user.click(screen.getByRole("button", { name: "批准执行" }));
    await user.click(screen.getByRole("button", { name: "返回中枢总览" }));
    await user.click(screen.getByRole("button", { name: "打开敕令详情" }));

    expect(screen.getByRole("status")).toHaveTextContent("已批准执行");
    expect(screen.getByRole("textbox", { name: "裁决理由" })).toHaveValue("仅运行既定测试");
    expect(screen.getByRole("textbox", { name: "裁决理由" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "恢复初始状态" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "裁决理由" })).toBeEnabled();
  });

  it("describes local evolution choices without claiming persisted reasons", async () => {
    const user = userEvent.setup();
    render(<App initialScreen="evolution" />);

    await user.click(screen.getByRole("button", { name: "继续观察" }));

    expect(screen.getByRole("status")).toHaveTextContent(
      "本地验收状态已更新，尚未写入真实演化档案",
    );
    expect(screen.queryByText("裁决理由已写入演化档案")).not.toBeInTheDocument();
  });

  it("does not claim a rejected decision was persisted to a real timeline", async () => {
    const user = userEvent.setup();
    render(<App initialScreen="edict" />);

    await user.click(screen.getByRole("button", { name: "驳回" }));

    expect(screen.getByRole("status")).toHaveTextContent(
      "本地验收状态已更新，尚未写入真实治理时间线",
    );
    expect(screen.queryByText("裁决结果已写入治理时间线")).not.toBeInTheDocument();
  });
});
