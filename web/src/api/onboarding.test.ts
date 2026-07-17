import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getReadiness: vi.fn(),
  listPersonas: vi.fn(),
  listSkills: vi.fn(),
  listEdicts: vi.fn(),
}));

vi.mock("./health", () => ({ getReadiness: apiMocks.getReadiness }));
vi.mock("./personas", () => ({ listPersonas: apiMocks.listPersonas }));
vi.mock("./system", () => ({ listSkills: apiMocks.listSkills }));
vi.mock("./edicts", () => ({ listEdicts: apiMocks.listEdicts }));

import { getOnboardingState } from "./onboarding";

const personas = [
  ["bingbu", "兵部"],
  ["ducha", "都察院"],
  ["hubu", "户部"],
  ["neige", "内阁"],
  ["tongzheng", "通政司"],
  ["wenyuan", "文渊阁"],
].map(([id, name]) => ({ id, name, department: id }));

const skills = [
  { name: "file-ops", source: "builtin" },
  { name: "shell", source: "builtin" },
];

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getReadiness.mockResolvedValue({
    schema_version: "1",
    status: "ready",
    profile: "demo",
  });
  apiMocks.listPersonas.mockResolvedValue({ success: true, data: personas });
  apiMocks.listSkills.mockResolvedValue({ success: true, data: skills });
  apiMocks.listEdicts.mockResolvedValue({
    success: true,
    data: [],
    metadata: { total: 0, limit: 1, offset: 0 },
  });
});

describe("onboarding state composition", () => {
  it("derives a fresh install only from ready server truth and zero persisted edicts", async () => {
    await expect(getOnboardingState()).resolves.toMatchObject({
      required: true,
      profile: "demo",
      readiness: "ready",
      packagedPersonas: personas,
      builtinSkills: skills,
    });

    expect(apiMocks.listEdicts).toHaveBeenCalledWith({ limit: 1 });
  });

  it("does not require onboarding once an edict exists", async () => {
    apiMocks.listEdicts.mockResolvedValue({
      success: true,
      data: [{ id: "edict-existing" }],
      metadata: { total: 1, limit: 1, offset: 0 },
    });

    await expect(getOnboardingState()).resolves.toMatchObject({ required: false });
  });

  it("turns readiness not_ready into service-unavailable before catalog reads", async () => {
    apiMocks.getReadiness.mockResolvedValue({
      schema_version: "1",
      status: "not_ready",
      profile: "demo",
    });

    await expect(getOnboardingState()).rejects.toMatchObject({
      status: 503,
      code: "onboarding-readiness-unavailable",
    });
    expect(apiMocks.listPersonas).not.toHaveBeenCalled();
    expect(apiMocks.listSkills).not.toHaveBeenCalled();
    expect(apiMocks.listEdicts).not.toHaveBeenCalled();
  });

  it("treats readiness transport failure as service-unavailable", async () => {
    apiMocks.getReadiness.mockRejectedValue(new Error("connection refused"));

    await expect(getOnboardingState()).rejects.toMatchObject({
      status: 503,
      code: "onboarding-readiness-unavailable",
      retryable: true,
    });
  });

  it.each([401, 403])("preserves readiness permission failure %s", async (status) => {
    apiMocks.getReadiness.mockRejectedValue({
      status,
      code: status === 401 ? "auth-required" : "permission-denied",
      message: "",
      correlationId: null,
      retryable: false,
    });

    await expect(getOnboardingState()).rejects.toMatchObject({ status });
  });

  it("rejects missing or changed packaged personas instead of presenting a partial success", async () => {
    apiMocks.listPersonas.mockResolvedValue({
      success: true,
      data: personas.filter((persona) => persona.id !== "ducha"),
    });

    await expect(getOnboardingState()).rejects.toMatchObject({
      status: 503,
      code: "onboarding-resources-unavailable",
    });
  });

  it("rejects an extra persona because the catalog exposes no authoritative source marker", async () => {
    apiMocks.listPersonas.mockResolvedValue({
      success: true,
      data: [
        ...personas,
        { id: "custom", name: "自定义官员", department: "bingbu" },
      ],
    });

    await expect(getOnboardingState()).rejects.toMatchObject({
      status: 503,
      code: "onboarding-resources-unavailable",
    });
  });

  it("rejects a duplicate packaged persona id instead of collapsing it", async () => {
    apiMocks.listPersonas.mockResolvedValue({
      success: true,
      data: [...personas, { ...personas[0] }],
    });

    await expect(getOnboardingState()).rejects.toMatchObject({
      status: 503,
      code: "onboarding-resources-unavailable",
    });
  });

  it("requires exactly the two builtin skills while ignoring user overlays", async () => {
    apiMocks.listSkills.mockResolvedValue({
      success: true,
      data: [...skills, { name: "user-helper", source: "user" }],
    });

    await expect(getOnboardingState()).resolves.toMatchObject({
      builtinSkills: skills,
    });

    apiMocks.listSkills.mockResolvedValue({
      success: true,
      data: [...skills, { name: "unexpected-builtin", source: "builtin" }],
    });
    await expect(getOnboardingState()).rejects.toMatchObject({
      status: 503,
      code: "onboarding-resources-unavailable",
    });
  });

  it("does not infer an empty install when authoritative edict metadata is absent", async () => {
    apiMocks.listEdicts.mockResolvedValue({ success: true, data: [] });

    await expect(getOnboardingState()).rejects.toMatchObject({
      status: 503,
      code: "onboarding-state-unavailable",
    });
  });
});
