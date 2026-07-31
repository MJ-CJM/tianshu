// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hookMocks = vi.hoisted(() => ({
  useSkills: vi.fn(),
  useSkillDetail: vi.fn(),
}));
const apiMocks = vi.hoisted(() => ({
  pinSkill: vi.fn(),
}));

vi.mock("../../hooks/useSystem", () => hookMocks);
vi.mock("../../api/system", () => apiMocks);

import SkillsTab from "./SkillsTab";

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
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Skills capability truth", () => {
  it("shows the loaded skill catalog as read-only", () => {
    hookMocks.useSkills.mockReturnValue({
      data: [
        {
          name: "manual-skill",
          description: "manual",
          source: "user",
          always: false,
          tool_tier: null,
          path: "/tmp/manual-skill",
          content_length: 42,
        },
        {
          name: "learned-skill",
          description: "learned",
          source: "user",
          always: false,
          tool_tier: null,
          path: "/tmp/learned-skill",
          content_length: 42,
          created_by: "agent",
          pinned: false,
        },
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    hookMocks.useSkillDetail.mockImplementation((name: string | null) => ({
      data: name
        ? {
            name,
            description: "manual",
            source: "user",
            always: false,
            tool_tier: null,
            path: "/tmp/manual-skill",
            content_length: 42,
            content: "# Read only",
          }
        : undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    }));

    render(<SkillsTab />);

    expect(screen.getByText(/治理候选/)).toBeInTheDocument();
    expect(screen.queryByText("新建技能")).not.toBeInTheDocument();
    expect(screen.getByText("learned-skill")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pin|钉/ })).toBeInTheDocument();
    const skillButton = screen.getByRole("button", { name: "manual-skill" });
    skillButton.focus();
    expect(skillButton).toHaveFocus();
    fireEvent.click(skillButton);
    expect(screen.getByRole("textbox")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: /保存/ })).not.toBeInTheDocument();
  });

});
