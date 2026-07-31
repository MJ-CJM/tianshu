// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const profileMocks = vi.hoisted(() => ({
  usePersonaProfile: vi.fn(),
}));

vi.mock("../../hooks/usePersonaProfile", () => profileMocks);
vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  };
});
vi.mock("../../i18n", () => ({
  useT: () => (key: string) => key,
}));

import ProfileTab from "./ProfileTab";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ProfileTab empty state", () => {
  it("keeps the promised synthesis action reachable before a profile exists", () => {
    profileMocks.usePersonaProfile.mockReturnValue({
      data: { exists: false },
      isLoading: false,
      error: null,
    });

    render(<ProfileTab personaId="qa_officer" />);

    expect(screen.getByText("comp.profile.empty")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /comp\.profile\.synthesize/ }),
    ).toBeInTheDocument();
  });
});
