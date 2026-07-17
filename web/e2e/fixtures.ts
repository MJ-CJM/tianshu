import AxeBuilder from "@axe-core/playwright";
import { expect, test as base, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(webRoot, "..");
const python = join(repoRoot, ".venv", "bin", "python");
const staticDir = join(repoRoot, "src", "tianshu", "web", "static");

export interface DemoStack {
  baseURL: string;
  artifactDir: string;
  dbPath: string;
  stop: () => Promise<void>;
}

type Fixtures = {
  stack: DemoStack;
  visualStack: DemoStack;
  isolatedStack: DemoStack;
};

export const VIEWPORTS = [
  { name: "1280x800", size: { width: 1280, height: 800 } },
  { name: "1440x1024", size: { width: 1440, height: 1024 } },
] as const;
export const THEMES = ["dark", "light"] as const;
export const CORE_ROUTES = [
  { name: "control", path: "/control" },
  { name: "edict-detail", path: "/edicts/:fixture" },
  { name: "evolution", path: "/evolution" },
] as const;

async function freePort(): Promise<number> {
  return await new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("failed to allocate a loopback port"));
        return;
      }
      server.close((error) => error ? reject(error) : resolvePort(address.port));
    });
  });
}

async function waitForReady(baseURL: string, child: ChildProcess, logs: () => string): Promise<void> {
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`demo stack exited during startup (${child.exitCode})\n${logs()}`);
    }
    try {
      const live = await fetch(`${baseURL}/health/live`);
      const ready = await fetch(`${baseURL}/health/ready`);
      if (live.ok && ready.ok) return;
    } catch {
      // The loopback listener is not ready yet.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  }
  throw new Error(`demo stack did not become ready\n${logs()}`);
}

async function stopProcess(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.pid === undefined) return;
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    child.kill("SIGTERM");
  }
  await Promise.race([
    new Promise<void>((resolveExit) => child.once("exit", () => resolveExit())),
    new Promise<void>((resolveTimeout) => setTimeout(resolveTimeout, 5_000)),
  ]);
  if (child.exitCode === null) {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {
      child.kill("SIGKILL");
    }
  }
}

async function launchDemoStack(): Promise<DemoStack> {
  const home = await mkdtemp(join(tmpdir(), "tianshu-s4-web-"));
  const port = await freePort();
  const baseURL = `http://127.0.0.1:${port}`;
  const paths = {
    artifacts: join(home, "artifacts"),
    logs: join(home, "logs"),
    memory: join(home, "memory"),
    personas: join(home, "personas"),
    plugins: join(home, "plugins"),
    staging: join(home, "workspaces"),
    workspace: join(home, "workspace"),
  };
  await Promise.all(Object.values(paths).map((path) => mkdir(path, { recursive: true })));

  let output = "";
  const remember = (chunk: Buffer) => {
    output = `${output}${chunk.toString("utf8")}`.slice(-16_000);
  };
  const child = spawn(
    python,
    ["-m", "uvicorn", "tianshu.app:create_app", "--factory", "--host", "127.0.0.1", "--port", String(port), "--log-level", "warning"],
    {
      cwd: repoRoot,
      detached: true,
      env: {
        ...process.env,
        HOME: home,
        PYTHONPATH: join(repoRoot, "src"),
        TIANSHU_HOME: home,
        TIANSHU_STARTUP_PROFILE: "demo",
        TIANSHU_SECURITY_MODE: "trusted-local",
        TIANSHU_HOST: "127.0.0.1",
        TIANSHU_PORT: String(port),
        TIANSHU_DB_PATH: join(home, "tianshu.db"),
        TIANSHU_ARTIFACT_DIR: paths.artifacts,
        TIANSHU_LOG_DIR: paths.logs,
        TIANSHU_MEMORY_DIR: paths.memory,
        TIANSHU_RUNTIME_PERSONAS_DIR: paths.personas,
        TIANSHU_PLUGINS_DIR: paths.plugins,
        TIANSHU_WORKSPACE_STAGING_ROOT: paths.staging,
        TIANSHU_WORKSPACE_DIR: paths.workspace,
        TIANSHU_STATIC_DIR: staticDir,
        TIANSHU_TELEMETRY: "off",
        TIANSHU_LOG_LEVEL: "WARNING",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  child.stdout?.on("data", remember);
  child.stderr?.on("data", remember);

  try {
    await waitForReady(baseURL, child, () => output);
  } catch (error) {
    await stopProcess(child);
    await rm(home, { force: true, recursive: true });
    throw error;
  }

  let stopped = false;
  return {
    baseURL,
    artifactDir: paths.artifacts,
    dbPath: join(home, "tianshu.db"),
    stop: async () => {
      if (stopped) return;
      stopped = true;
      await stopProcess(child);
      await rm(home, { force: true, recursive: true });
    },
  };
}

export const test = base.extend<Fixtures>({
  stack: [
    async ({}, use) => {
      const stack = await launchDemoStack();
      try {
        await use(stack);
      } finally {
        await stack.stop();
      }
    },
    { scope: "worker" },
  ],
  visualStack: [
    async ({}, use) => {
      const stack = await launchDemoStack();
      try {
        await use(stack);
      } finally {
        await stack.stop();
      }
    },
    { scope: "worker" },
  ],
  isolatedStack: async ({}, use) => {
    const stack = await launchDemoStack();
    try {
      await use(stack);
    } finally {
      await stack.stop();
    }
  },
  page: async ({ page }, use) => {
    const failures: string[] = [];
    const isAssertedMissingDag = (url: string) =>
      /^\/api\/dag\/by-edict\/[^/]+$/.test(new URL(url).pathname);
    await page.addInitScript(() => {
      localStorage.setItem("tianshu-locale", "en");
    });
    page.on("console", (message) => {
      if (
        message.type() === "error" &&
        !(isAssertedMissingDag(message.location().url) && message.text().includes("404"))
      ) {
        failures.push(`console: ${message.text()}`);
      }
    });
    page.on("pageerror", (error) => failures.push(`pageerror: ${error.message}`));
    page.on("requestfailed", (request) => {
      failures.push(`requestfailed: ${request.method()} ${request.url()} ${request.failure()?.errorText ?? ""}`);
    });
    page.on("response", (response) => {
      if (isAssertedMissingDag(response.url())) {
        expect(response.status(), "an Edict without a DAG has the asserted optional-read 404").toBe(404);
        return;
      }
      if (response.status() >= 400) {
        failures.push(`response: ${response.status()} ${response.request().method()} ${response.url()}`);
      }
    });
    await use(page);
    expect(failures, "browser console and network failures").toEqual([]);
  },
});

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${url} returned ${response.status}: ${await response.text()}`);
  return await response.json() as T;
}

type CreatedEdict = { edictId: string; decisionId?: string };
type PlanReviewEdict = { edictId: string; decisionId: string; decisionVersion: number };

async function createEdict(stack: DemoStack, planReview: boolean): Promise<CreatedEdict> {
  const key = crypto.randomUUID();
  const response = await jsonRequest<{ data: { id: string } }>(`${stack.baseURL}/api/edicts`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": key },
    body: JSON.stringify({
      title: planReview ? "S4 裁决测试敕令" : "S4 核心详情测试敕令",
      goal: planReview ? "生成受治理的计划复核证据" : "生成确定性的核心页面证据",
      priority: "normal",
      review_policy: "always",
      plan_review: planReview,
      idempotency_key: key,
    }),
  });
  return { edictId: response.data.id };
}

export async function createPlanReviewEdict(stack: DemoStack): Promise<PlanReviewEdict> {
  const created = await createEdict(stack, true);
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    const detail = await jsonRequest<{ data: { decisions: Array<{ request: { decision_request_id: string; status: string; version: number } }> } }>(
      `${stack.baseURL}/api/edicts/${encodeURIComponent(created.edictId)}/detail`,
    );
    const pending = detail.data.decisions.find((item) => item.request.status === "pending");
    if (pending) {
      return {
        edictId: created.edictId,
        decisionId: pending.request.decision_request_id,
        decisionVersion: pending.request.version,
      };
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 200));
  }
  throw new Error(`Edict ${created.edictId} did not produce a pending Decision`);
}

export async function waitForClosedEvidence(
  stack: DemoStack,
  edictId: string,
): Promise<{ bundleId: string; contentHash: string }> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const detail = await jsonRequest<{ data: { evidence: Array<{ bundle_id: string; status: string; content_hash: string | null }> } }>(
      `${stack.baseURL}/api/edicts/${encodeURIComponent(edictId)}/detail`,
    );
    const closed = detail.data.evidence.find((item) => item.status === "closed" && item.content_hash);
    if (closed?.content_hash) return { bundleId: closed.bundle_id, contentHash: closed.content_hash };
    await new Promise((resolveWait) => setTimeout(resolveWait, 250));
  }
  throw new Error(`Edict ${edictId} did not close an Evidence bundle`);
}

export async function seedClosedEvidence(
  stack: DemoStack,
  edictId: string,
  pendingDecisionId?: string,
): Promise<void> {
  const script = [
    "import sys",
    "from datetime import UTC, datetime",
    "from tianshu.evidence.service import ArtifactStore, EvidenceService",
    "from tianshu.executor.capabilities import get_executor_manifest, probe_host_capabilities, resolve_governance_contract",
    "from tianshu.models import AuditResult, TaskStatus",
    "from tianshu.models.decision import DecisionResolutionV1, DecisionStatus",
    "from tianshu.models.run_state import RunPhase",
    "from tianshu.storage import Storage",
    "db_path, artifact_dir, edict_id, decision_id = sys.argv[1:5]",
    "storage = Storage(db_path)",
    "storage.init_db()",
    "memorial = storage.get_memorial_by_edict(edict_id)",
    "assert memorial is not None, 'fixture memorial missing'",
    "edict = storage.get_edict(edict_id)",
    "assert edict is not None and edict.governance_contract is not None, 'fixture contract missing'",
    "effective = resolve_governance_contract(edict.governance_contract, get_executor_manifest('native'), probe_host_capabilities())",
    "if storage.get_effective_governance_contract(memorial.id) is None:",
    "    storage.save_effective_governance_contract(memorial.id, edict.id, effective)",
    "now = datetime.now(UTC)",
    "if decision_id:",
    "    with storage.unit_of_work() as unit_of_work:",
    "        record = storage.decision_repo.get(unit_of_work.connection, decision_id)",
    "        assert record is not None, 'fixture Decision missing'",
    "        if record.request.status is DecisionStatus.PENDING:",
    "            storage.decision_repo.resolve(unit_of_work.connection, DecisionResolutionV1(decision_request_id=decision_id, action='approve', reason='S4 core route fixture approved', payload={'schema_version': 1}, actor_principal_id='local:owner', actor_display_name='Local Owner', resolved_at=now), expected_version=record.request.version, now=now)",
    "        unit_of_work.commit()",
    "completed_memorial = memorial.model_copy(update={'status': TaskStatus.COMPLETED, 'completed_at': now, 'audit': AuditResult(verdict='pass', rules_checked=1)})",
    "storage.update_memorial(completed_memorial)",
    "with storage.unit_of_work() as unit_of_work:",
    "    state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)",
    "    assert state is not None, 'fixture RunState missing'",
    "    decision_id = state.continuation.pending_decision_id or state.continuation.resolved_decision_id",
    "    continuation = state.continuation.model_copy(update={'pending_decision_id': None, 'resolved_decision_id': decision_id})",
    "    terminal = state.model_copy(update={'phase': RunPhase.COMPLETED, 'continuation': continuation, 'updated_at': now})",
    "    storage.run_state_repo.compare_and_swap(unit_of_work.connection, terminal, expected_version=state.version)",
    "    unit_of_work.commit()",
    "artifacts = ArtifactStore(artifact_dir, storage.artifact_repo, storage.unit_of_work, max_object_bytes=104857600, max_total_bytes=5368709120)",
    "service = EvidenceService(storage, artifacts)",
    "opened = service.build_open(memorial.id)",
    "service.close(memorial.id, expected_version=opened.version)",
    "storage.close()",
  ].join("\n");
  await new Promise<void>((resolveSeed, reject) => {
    let stderr = "";
    const child = spawn(python, ["-c", script, stack.dbPath, stack.artifactDir, edictId, pendingDecisionId ?? ""], {
      cwd: repoRoot,
      env: { ...process.env, PYTHONPATH: join(repoRoot, "src") },
      stdio: ["ignore", "ignore", "pipe"],
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      stderr = `${stderr}${chunk.toString("utf8")}`.slice(-8_000);
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) resolveSeed();
      else reject(new Error(`canonical Evidence fixture failed (${code})\n${stderr}`));
    });
  });
}

const coreEdicts = new Map<string, Promise<string>>();
async function coreEdictId(stack: DemoStack): Promise<string> {
  let pending = coreEdicts.get(stack.baseURL);
  if (!pending) {
    pending = (async () => {
      const created = await createPlanReviewEdict(stack);
      await seedClosedEvidence(stack, created.edictId, created.decisionId);
      await waitForClosedEvidence(stack, created.edictId);
      return created.edictId;
    })();
    coreEdicts.set(stack.baseURL, pending);
  }
  return await pending;
}

export async function setShellState(
  page: Page,
  state: {
    theme: "dark" | "light";
    collapsed: boolean;
    locale?: "zh-classic" | "zh-modern" | "en";
  },
): Promise<void> {
  await page.addInitScript((next) => {
    localStorage.setItem("tianshu-locale", next.locale ?? "en");
    localStorage.setItem("tianshu-theme", next.theme);
    localStorage.setItem("tianshu-sidebar-collapsed", String(next.collapsed));
  }, state);
}

export async function prepareCoreRoute(
  page: Page,
  stack: DemoStack,
  route: (typeof CORE_ROUTES)[number],
): Promise<void> {
  const path = route.name === "edict-detail"
    ? `/edicts/${encodeURIComponent(await coreEdictId(stack))}`
    : route.path;
  await page.goto(`${stack.baseURL}${path}`);
  await expect(page.locator("main")).toBeVisible();
  await expect(page.getByText("Loading", { exact: true })).toHaveCount(0, { timeout: 30_000 });
}

export async function installBlockedEvolutionContract(page: Page): Promise<void> {
  await page.route("**/api/evolution", async (route) => {
    expect(new URL(route.request().url()).pathname).toBe("/api/evolution");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        correlation_id: "correlation:s4-e2e-blocked",
        data: {
          schema_version: 1,
          status: "degraded",
          reason_code: "blocking_gate_failed",
          candidates: [{
            candidate_id: "candidate:s4-blocked",
            kind: "policy",
            version: 1,
            lifecycle: "blocked",
            artifact_hash: "sha256:s4-blocked-artifact",
            promotion_allowed: false,
            rollback_state: "ready",
            gates: [{
              code: "gate:evidence",
              status: "failed",
              blocking: true,
              current: 0,
              required: 1,
              evidence_bundle_id: null,
              evidence_hash: null,
            }],
          }],
          routing: [],
          last_gate_hash: "sha256:s4-blocked-gate",
        },
      }),
    });
  });
}

export async function assertAxeClean(page: Page): Promise<void> {
  const result = await new AxeBuilder({ page }).analyze();
  const violations = result.violations.filter((item) => item.impact === "serious" || item.impact === "critical");
  expect(violations, "axe serious/critical violations").toEqual([]);
}

export async function assertKeyboardActionsHaveVisibleFocus(page: Page): Promise<void> {
  const expected = await page.locator(
    'a[href]:visible, button:not([disabled]):visible, input:not([disabled]):visible, textarea:not([disabled]):visible, select:not([disabled]):visible, [tabindex]:not([tabindex="-1"]):visible',
  ).evaluateAll((elements) => elements.filter((element) => (element as HTMLElement).tabIndex >= 0).length);
  const reached = new Set<string>();
  for (let index = 0; index < expected + 8; index += 1) {
    await page.keyboard.press("Tab");
    const state = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      if (!active || active === document.body) return null;
      const activeRect = active.getBoundingClientRect();
      const target = activeRect.width > 0 && activeRect.height > 0
        ? active
        : active.closest<HTMLElement>("label");
      if (!target) return null;
      const style = getComputedStyle(target);
      const rect = target.getBoundingClientRect();
      const focusables = Array.from(document.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ));
      return {
        key: `${focusables.indexOf(active)}:${target.tagName}:${active.getAttribute("aria-label") ?? target.textContent?.trim() ?? ""}:${active.getAttribute("href") ?? ""}`,
        visible: rect.width > 0 && rect.height > 0,
        focusVisible: active.matches(":focus-visible") && style.outlineStyle !== "none" && Number.parseFloat(style.outlineWidth) > 0,
      };
    });
    if (!state) continue;
    expect(state.visible, `focused action ${state.key} is visible`).toBe(true);
    expect(state.focusVisible, `focused action ${state.key} has a visible focus ring`).toBe(true);
    reached.add(state.key);
  }
  expect(reached.size, "keyboard reaches every tabbable action").toBeGreaterThanOrEqual(expected);
}

export async function assertZoomHasNoPrimaryHorizontalTrap(page: Page): Promise<void> {
  const original = page.viewportSize() ?? { width: 1280, height: 800 };
  await page.setViewportSize({ width: Math.floor(original.width / 2), height: Math.floor(original.height / 2) });
  await page.waitForTimeout(100);
  const result = await page.evaluate(() => {
    const header = document.querySelector("header") as HTMLElement | null;
    const sidebar = document.querySelector("aside") as HTMLElement | null;
    const main = document.querySelector("main") as HTMLElement | null;
    const collapse = document.querySelector('button[aria-label="Collapse"], button[aria-label="Expand"]') as HTMLElement | null;
    const visible = (element: HTMLElement | null) => {
      if (!element) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.left < window.innerWidth && rect.top < window.innerHeight;
    };
    return {
      header: visible(header),
      sidebar: visible(sidebar),
      collapse: visible(collapse),
      primaryOverflow: main ? main.scrollWidth - main.clientWidth : Number.POSITIVE_INFINITY,
      overflowElements: main ? Array.from(main.querySelectorAll<HTMLElement>("*"))
        .filter((element) => element.scrollWidth - element.clientWidth > 1)
        .slice(0, 12)
        .map((element) => ({
          tag: element.tagName,
          className: element.className,
          overflow: element.scrollWidth - element.clientWidth,
          text: element.textContent?.trim().slice(0, 80),
        })) : [],
    };
  });
  expect(result.header, "header remains visible at 200% zoom").toBe(true);
  expect(result.sidebar, "sidebar remains visible at 200% zoom").toBe(true);
  expect(result.collapse, "sidebar control remains visible at 200% zoom").toBe(true);
  expect(
    result.primaryOverflow,
    `primary content has no horizontal trap at 200% zoom; offenders=${JSON.stringify(result.overflowElements)}`,
  ).toBeLessThanOrEqual(1);
}

export { expect };
