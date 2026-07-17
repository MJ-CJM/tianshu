import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "./fixtures";

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
  await expect(page.getByRole("heading", { name: "Governance Contract" })).toBeVisible();
  await expect(page.getByText("Write the deterministic S4 browser-gate artifact").first()).toBeVisible();
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
