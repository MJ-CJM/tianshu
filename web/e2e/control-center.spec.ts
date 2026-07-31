import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

import { expect, test } from "./fixtures";

const KIB = 1024;
const ROUTE_CHUNK_CEILINGS_KIB = {
  ControlCenterPage: { minified: 7, gzip: 2.25 },
  EvolutionCenterPage: { minified: 7, gzip: 2.25 },
  EdictDetailPage: { minified: 70, gzip: 20 },
  DagBattleMapPage: { minified: 220, gzip: 72 },
} as const;

type ViteManifestChunk = {
  file: string;
  src?: string;
};

test("fresh onboarding creates the first governed Edict on the real demo stack", async ({
  isolatedStack,
  page,
}) => {
  await page.goto(`${isolatedStack.baseURL}/onboarding`);
  await expect(page.getByRole("heading", { name: "First-time setup" })).toBeVisible();
  await page.getByRole("radio", { name: "Demo profile" }).check();
  await page.getByLabel("Goal").fill("Write the deterministic S4 browser-gate artifact");
  await page.getByRole("button", { name: "New Task" }).click();
  await expect(page.getByRole("heading", { name: "Requested contract" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Effective contract" })).toBeVisible();
  await page.getByRole("button", { name: "Confirm contract and dispatch" }).click();

  await expect(page).toHaveURL(/\/edicts\/[^/]+$/);
  await expect(page.getByRole("heading", { name: "Task Detail" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Governance & audit/ })).toBeVisible();
  await expect(page.getByText("Write the deterministic S4 browser-gate artifact").first()).toBeVisible();
});

test("root entry accepts a returning user with a custom persona", async ({
  isolatedStack,
  page,
}) => {
  const persona = await page.request.post(`${isolatedStack.baseURL}/api/personas`, {
    data: {
      id: "custom-reviewer",
      name: "Custom Reviewer",
      department: "ducha",
      tool_tier_max: 1,
    },
  });
  expect(persona.ok()).toBe(true);

  const edict = await page.request.post(`${isolatedStack.baseURL}/api/edicts`, {
    headers: { "Idempotency-Key": crypto.randomUUID() },
    data: {
      title: "Returning user task",
      goal: "Verify that extensions never block the established workspace",
      review_policy: "never",
    },
  });
  expect(edict.ok()).toBe(true);

  await page.goto(`${isolatedStack.baseURL}/`);

  await expect(page).toHaveURL(`${isolatedStack.baseURL}/control`);
  await expect(page.getByRole("heading", { name: "Control Center" })).toBeVisible();
});

test("product source does not import mockData", async () => {
  const sourceFiles: string[] = [];
  const visit = (directory: string) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (/\.tsx?$/.test(entry.name)) sourceFiles.push(path);
    }
  };
  visit(join(process.cwd(), "src"));
  const offenders = sourceFiles.filter((file) => /(?:from|import\s*)[\s\S]*mockData/.test(
    readFileSync(file, "utf8"),
  ));
  expect(offenders).toEqual([]);
});

test("a stored locale survives reload instead of being overwritten by the E2E default", async ({
  stack,
  page,
}) => {
  await page.goto(`${stack.baseURL}/control`);
  await expect(page.getByRole("heading", { name: "Control Center" })).toBeVisible();
  await page.evaluate(() => localStorage.setItem("tianshu-locale", "zh-classic"));
  await page.reload();

  await expect(page.getByRole("heading", { name: "中枢总览" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("tianshu-locale"))).toBe("zh-classic");
});

test("production core route chunks stay within documented KiB ceilings", async () => {
  const staticRoot = join(process.cwd(), "..", "src", "tianshu", "web", "static");
  const manifest = JSON.parse(
    readFileSync(join(staticRoot, "manifest.json"), "utf8"),
  ) as Record<string, ViteManifestChunk>;

  for (const [route, ceilings] of Object.entries(ROUTE_CHUNK_CEILINGS_KIB)) {
    const chunk = Object.values(manifest).find((entry) =>
      entry.src?.endsWith(`/pages/${route}.tsx`)
    );
    expect(chunk, `${route} must be recorded in the production Vite manifest`).toBeDefined();
    const bytes = readFileSync(join(staticRoot, chunk!.file));
    const minifiedKib = statSync(join(staticRoot, chunk!.file)).size / KIB;
    const gzipKib = gzipSync(bytes).byteLength / KIB;

    expect(
      minifiedKib,
      `${route} minified ${minifiedKib.toFixed(2)} KiB exceeds ${ceilings.minified.toFixed(2)} KiB`,
    ).toBeLessThanOrEqual(ceilings.minified);
    expect(
      gzipKib,
      `${route} gzip ${gzipKib.toFixed(2)} KiB exceeds ${ceilings.gzip.toFixed(2)} KiB`,
    ).toBeLessThanOrEqual(ceilings.gzip);
  }
});

test("Control Center initial load keeps large deferred route chunks out of the critical path", async ({
  stack,
  page,
}) => {
  const scriptPaths: string[] = [];
  page.on("response", (response) => {
    if (response.request().resourceType() === "script") {
      scriptPaths.push(new URL(response.url()).pathname);
    }
  });

  await page.goto(`${stack.baseURL}/control`);
  await expect(page.getByRole("heading", { name: "Control Center" })).toBeVisible();
  expect(scriptPaths.some((path) => path.includes("ControlCenterPage-"))).toBe(true);
  expect(scriptPaths.filter((path) => /(?:DagBattleMapPage|EdictDetailPage|PersonaDashboardPage|SystemManagementPage)-/.test(path))).toEqual([]);
});
