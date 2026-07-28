import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.hoisted(() => vi.fn());

vi.mock("./client", () => ({ default: { post } }));

import { previewPersonaImport } from "./personas";

describe("previewPersonaImport", () => {
  beforeEach(() => post.mockReset());

  it("posts source + path and returns the draft", async () => {
    const draft = {
      source: "hermes",
      soul_body: "务实的工程助手",
      role_body: "",
      suggested_name: "赫尔墨斯",
      suggested_model: "anthropic/claude-opus-4",
      skills: [{ name: "ci-runner", source_dir: "/s/ci", description: "run CI" }],
      source_notes: ["已排除(运行态/自进化,不导入): memories/ 长期记忆"],
    };
    post.mockResolvedValue({ data: { data: draft } });

    await expect(previewPersonaImport("hermes", "~/.hermes")).resolves.toEqual(draft);
    expect(post).toHaveBeenCalledWith("/personas/import/preview", {
      source: "hermes",
      path: "~/.hermes",
    });
  });

  it("omits path when not provided (server auto-detects)", async () => {
    post.mockResolvedValue({ data: { data: { source: "openclaw" } } });
    await previewPersonaImport("openclaw");
    expect(post).toHaveBeenCalledWith("/personas/import/preview", {
      source: "openclaw",
      path: undefined,
    });
  });
});
