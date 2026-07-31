// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hookMocks = vi.hoisted(() => ({
  usePersonas: vi.fn(),
  usePromptLayers: vi.fn(),
  usePromptFiles: vi.fn(),
  usePromptFileContent: vi.fn(),
  useUpdatePromptFile: vi.fn(),
  usePromptPreview: vi.fn(),
}));

vi.mock("../../hooks/usePersonas", () => ({
  usePersonas: hookMocks.usePersonas,
}));
vi.mock("../../hooks/useOps", () => ({
  usePromptLayers: hookMocks.usePromptLayers,
}));
vi.mock("../../hooks/useSystem", () => ({
  usePromptFiles: hookMocks.usePromptFiles,
  usePromptFileContent: hookMocks.usePromptFileContent,
  useUpdatePromptFile: hookMocks.useUpdatePromptFile,
  usePromptPreview: hookMocks.usePromptPreview,
}));

import SystemPromptTab from "./SystemPromptTab";

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

describe("SystemPromptTab prompt drafts", () => {
  it("saves the loaded content unchanged and preserves an intentional empty draft", async () => {
    const update = vi.fn();
    hookMocks.usePersonas.mockReturnValue({ data: [] });
    hookMocks.usePromptLayers.mockReturnValue({ data: undefined, isLoading: false });
    hookMocks.usePromptFiles.mockReturnValue({
      data: {
        files: [{ persona_id: "court", filename: "COURT.md", size: 42 }],
        departments: { court: "朝廷" },
      },
    });
    hookMocks.usePromptFileContent.mockReturnValue({
      data: { content: "# Original prompt" },
      isLoading: false,
    });
    hookMocks.useUpdatePromptFile.mockReturnValue({ mutate: update, isPending: false });
    hookMocks.usePromptPreview.mockReturnValue({ data: undefined, isLoading: false });

    const user = userEvent.setup();
    const { container } = render(<SystemPromptTab />);
    const editButton = container.querySelector(".anticon-edit")?.closest("button");
    expect(editButton).not.toBeNull();
    await user.click(editButton!);

    const editor = await screen.findByRole("textbox");
    expect(editor).toHaveValue("# Original prompt");
    await user.click(screen.getByRole("button", { name: /保\s*存/ }));
    expect(update).toHaveBeenLastCalledWith(
      expect.objectContaining({ content: "# Original prompt" }),
      expect.any(Object),
    );

    await user.clear(editor);
    expect(editor).toHaveValue("");
    await user.click(screen.getByRole("button", { name: /保\s*存/ }));
    expect(update).toHaveBeenLastCalledWith(
      expect.objectContaining({ content: "" }),
      expect.any(Object),
    );
  });

  it("does not turn a prompt catalog outage into an empty catalog", () => {
    hookMocks.usePersonas.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: {
        status: 503,
        code: "service-unavailable",
        message: "提示词目录暂不可用",
        correlationId: "prompt-test",
        retryable: true,
      },
      refetch: vi.fn(),
    });
    hookMocks.usePromptLayers.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    hookMocks.usePromptFiles.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    hookMocks.usePromptFileContent.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    hookMocks.useUpdatePromptFile.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    });
    hookMocks.usePromptPreview.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<SystemPromptTab />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "提示词目录暂不可用",
    );
    expect(screen.queryByText(/暂无提示词文件/)).not.toBeInTheDocument();
  });
});
