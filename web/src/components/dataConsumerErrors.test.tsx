// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiProblem } from "../contracts/api";
import NetworkCapabilitySection from "./edict/NetworkCapabilitySection";
import OuterLoopTimeline from "./edict/OuterLoopTimeline";
import PolicyProfilePanel from "./policy/PolicyProfilePanel";
import ExternalCredentialsTab from "./system/ExternalCredentialsTab";

const apiMocks = vi.hoisted(() => ({
  fetchPolicyTemplates: vi.fn(),
  getOuterLoopIterations: vi.fn(),
  listCredentials: vi.fn(),
}));

vi.mock("../api/policy", () => ({
  fetchPolicyTemplates: apiMocks.fetchPolicyTemplates,
}));
vi.mock("../api/edicts", () => ({
  getOuterLoopIterations: apiMocks.getOuterLoopIterations,
}));
vi.mock("../api/credentials", () => ({
  listCredentials: apiMocks.listCredentials,
  createCredential: vi.fn(),
  deleteCredential: vi.fn(),
  updateCredential: vi.fn(),
}));
vi.mock("../hooks/useWebSocket", () => ({
  useWebSocket: () => ({ lastMessage: null }),
}));

function problem(status: number, code: string, message: string): ApiProblem {
  return {
    status,
    code,
    message,
    correlationId: `corr-${status}`,
    retryable: status >= 500,
  };
}

function renderWithQueryClient(ui: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
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
  apiMocks.fetchPolicyTemplates.mockReset();
  apiMocks.getOuterLoopIterations.mockReset();
  apiMocks.listCredentials.mockReset();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("truthful production data states", () => {
  it("does not present a 403 policy-template failure as an empty template list", async () => {
    apiMocks.fetchPolicyTemplates.mockRejectedValue(
      problem(403, "permission-denied", "模板只对管理员开放"),
    );

    render(<PolicyProfilePanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无权查看此内容");
    expect(screen.getByRole("alert")).toHaveTextContent("模板只对管理员开放");
  });

  it("does not hide a 401 outer-loop failure as a successful empty timeline", async () => {
    apiMocks.getOuterLoopIterations.mockRejectedValue(
      problem(401, "auth-required", "登录已失效"),
    );

    render(<OuterLoopTimeline edictId="edict-1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无权查看此内容");
    expect(screen.getByRole("alert")).toHaveTextContent("登录已失效");
  });

  it("keeps a successful empty outer-loop response silent", async () => {
    apiMocks.getOuterLoopIterations.mockResolvedValue({ data: [] });

    const { container } = render(<OuterLoopTimeline edictId="edict-1" />);

    await vi.waitFor(() => expect(apiMocks.getOuterLoopIterations).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("does not present a 503 credential-host failure as no configured hosts", async () => {
    apiMocks.listCredentials.mockRejectedValue(
      problem(503, "service-unavailable", "凭证服务暂不可用"),
    );

    render(
      <NetworkCapabilitySection
        profileTemplate="trusted-automation"
        apiRequestHosts={[]}
        apiRequestWriteHosts={[]}
        onChange={vi.fn()}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.getByRole("alert")).toHaveTextContent("凭证服务暂不可用");
  });

  it("recognizes the direct ApiProblem 503 vault-unavailable response", async () => {
    apiMocks.listCredentials.mockRejectedValue(
      problem(503, "vault-unavailable", "secret storage is offline"),
    );

    renderWithQueryClient(<ExternalCredentialsTab />);

    expect(
      await screen.findByText("尚未配置主密钥 TIANSHU_SECRET_MASTER_KEY"),
    ).toBeInTheDocument();
  });
});
