// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const personaHooks = vi.hoisted(() => ({
  usePersonas: vi.fn(),
  useRegeneratePersonaIdentity: vi.fn(),
  usePersonaMetrics: vi.fn(),
  useUpdatePersona: vi.fn(),
}));
const systemHooks = vi.hoisted(() => ({
  usePromptFiles: vi.fn(),
  usePromptFileContent: vi.fn(),
  useUpdatePromptFile: vi.fn(),
  usePromptPreview: vi.fn(),
  useTools: vi.fn(),
  useSkills: vi.fn(),
}));
const opsHooks = vi.hoisted(() => ({ usePromptLayers: vi.fn() }));
const mcpHooks = vi.hoisted(() => ({ useMCPServers: vi.fn() }));
const departmentHooks = vi.hoisted(() => ({ useDepartments: vi.fn() }));
const configHooks = vi.hoisted(() => ({ useConfigs: vi.fn() }));
const memoryHooks = vi.hoisted(() => ({
  usePersonaMemorials: vi.fn(),
  usePersonaMemory: vi.fn(),
  useDeleteMemory: vi.fn(),
  useRecallMemory: vi.fn(),
}));

vi.mock("../hooks/usePersonas", () => personaHooks);
vi.mock("../hooks/useSystem", () => systemHooks);
vi.mock("../hooks/useOps", () => opsHooks);
vi.mock("../hooks/useMCP", () => mcpHooks);
vi.mock("../hooks/useDepartments", () => departmentHooks);
vi.mock("../hooks/useConfig", () => configHooks);
vi.mock("../hooks/useMemory", () => memoryHooks);
vi.mock("../components/persona/ProfileTab", () => ({ default: () => null }));

import PersonaDetailPage from "./PersonaDetailPage";

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

describe("PersonaDetailPage prompt drafts", () => {
  it("saves the loaded content unchanged and preserves an intentional empty draft", async () => {
    const update = vi.fn();
    personaHooks.usePersonas.mockReturnValue({
      data: [
        {
          id: "officer-1",
          name: "御史甲",
          department: "ducha",
          department_name: "都察院",
          title: "御史",
          tools_allowed: [],
          tools_denied: [],
          skills_allowed: [],
          tool_tier_max: 1,
          can_delegate: false,
          memory_global_read: false,
          delegates_to: [],
          llm_config_name: null,
        },
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    personaHooks.usePersonaMetrics.mockReturnValue({ data: undefined, isLoading: false });
    personaHooks.useUpdatePersona.mockReturnValue({ mutate: vi.fn(), isPending: false });
    personaHooks.useRegeneratePersonaIdentity.mockReturnValue({ mutate: vi.fn(), isPending: false });
    systemHooks.useTools.mockReturnValue({ data: [] });
    systemHooks.useSkills.mockReturnValue({ data: [] });
    systemHooks.usePromptFiles.mockReturnValue({
      data: {
        files: [{ persona_id: "officer-1", filename: "SOUL.md", size: 64 }],
        departments: { ducha: "都察院" },
      },
    });
    systemHooks.usePromptFileContent.mockReturnValue({
      data: { content: "# Detail original" },
      isLoading: false,
    });
    systemHooks.useUpdatePromptFile.mockReturnValue({ mutate: update, isPending: false });
    systemHooks.usePromptPreview.mockReturnValue({ data: undefined, isLoading: false });
    opsHooks.usePromptLayers.mockReturnValue({ data: undefined, isLoading: false });
    mcpHooks.useMCPServers.mockReturnValue({ data: [] });
    departmentHooks.useDepartments.mockReturnValue({ data: [] });
    configHooks.useConfigs.mockReturnValue({ data: { configs: [] } });
    memoryHooks.usePersonaMemorials.mockReturnValue({ data: undefined, isLoading: false });
    memoryHooks.usePersonaMemory.mockReturnValue({ data: undefined, isLoading: false });
    memoryHooks.useDeleteMemory.mockReturnValue({ mutate: vi.fn(), isPending: false });
    memoryHooks.useRecallMemory.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/personas/officer-1"]}>
        <Routes>
          <Route path="/personas/:personaId" element={<PersonaDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("tab", { name: /指令文件|Prompt/ }));
    const filename = await screen.findByText("SOUL.md");
    const fileCard = filename.closest("div[style*='width: 280px']");
    const editButton = fileCard?.querySelector(".anticon-edit")?.closest("button");
    expect(editButton).not.toBeNull();
    await user.click(editButton!);

    const editor = await screen.findByRole("textbox");
    expect(editor).toHaveValue("# Detail original");
    await user.click(screen.getByRole("button", { name: /保\s*存/ }));
    expect(update).toHaveBeenLastCalledWith(
      expect.objectContaining({ content: "# Detail original" }),
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
});
