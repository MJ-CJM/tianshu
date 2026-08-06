// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const providerHooks = vi.hoisted(() => ({
  useProviders: vi.fn(),
  useDeleteProvider: vi.fn(),
}));
const configHooks = vi.hoisted(() => ({
  useConfigs: vi.fn(),
  useCreateConfig: vi.fn(),
  useUpdateNamedConfig: vi.fn(),
  useDeleteConfig: vi.fn(),
  useActivateConfig: vi.fn(),
}));
const modelHooks = vi.hoisted(() => ({
  useModelProviders: vi.fn(),
}));

vi.mock("../../hooks/useProviders", () => providerHooks);
vi.mock("../../hooks/useConfig", () => configHooks);
vi.mock("../../hooks/useModelProviders", () => modelHooks);
vi.mock("./ModelProvidersSection", () => ({ default: () => null }));
vi.mock("./ModelSelect", () => ({ default: () => null }));
vi.mock("./TaskSlotsSection", () => ({ default: () => null }));

import ProvidersTab from "./ProvidersTab";

const mutation = () => ({ mutate: vi.fn(), isPending: false });

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

  providerHooks.useDeleteProvider.mockReturnValue(mutation());
  configHooks.useCreateConfig.mockReturnValue(mutation());
  configHooks.useUpdateNamedConfig.mockReturnValue(mutation());
  configHooks.useDeleteConfig.mockReturnValue(mutation());
  configHooks.useActivateConfig.mockReturnValue(mutation());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ProvidersTab data truth", () => {
  it("does not turn a provider request failure into an empty registry", () => {
    providerHooks.useProviders.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: {
        status: 503,
        code: "service-unavailable",
        message: "模型供应商暂不可用",
        correlationId: "providers-test",
        retryable: true,
      },
      refetch: vi.fn(),
    });
    configHooks.useConfigs.mockReturnValue({
      data: { configs: [], active_name: "" },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    modelHooks.useModelProviders.mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<ProvidersTab />);

    expect(screen.getByRole("alert")).toHaveTextContent("模型供应商暂不可用");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
