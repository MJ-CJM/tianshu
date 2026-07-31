import { expect, installBlockedEvolutionContract, test } from "./fixtures";

test("Evolution reports the authoritative S5 empty state", async ({ stack, page }) => {
  await page.goto(`${stack.baseURL}/evolution`);
  await expect(page.getByRole("heading", { name: "Evolution Center" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Enabled, with no candidates yet" }),
  ).toBeVisible();
  await expect(page.getByText("s5_governed_evolution_not_enabled")).toHaveCount(0);
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
