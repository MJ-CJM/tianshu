import { expect, installBlockedEvolutionContract, test } from "./fixtures";

test("Evolution reports the authoritative pre-S5 disabled contract", async ({ stack, page }) => {
  await page.goto(`${stack.baseURL}/evolution`);
  await expect(page.getByRole("heading", { name: "Not enabled" })).toBeVisible();
  await expect(page.getByText("s5_governed_evolution_not_enabled")).toBeVisible();
});

test("Evolution renders a blocked candidate from only the named read contract", async ({
  stack,
  page,
}) => {
  await installBlockedEvolutionContract(page);
  await page.goto(`${stack.baseURL}/evolution`);
  await expect(page.getByText("candidate:s4-blocked")).toBeVisible();
  await expect(page.getByText("Promotion blocked")).toBeVisible();
  await expect(page.getByText("gate:evidence")).toBeVisible();
});
