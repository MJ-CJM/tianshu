import {
  expect,
  installKeqingContracts,
  setShellState,
  test,
} from "./fixtures";

const LAB_DESTINATIONS = [
  {
    menuName: /演化司\s*实验/,
    path: "/evolution",
    heading: "演化中心",
    menuMaturity: "实验",
    pageMaturity: "实验",
  },
  {
    menuName: /诸界台\s*实验/,
    path: "/universes",
    heading: "位面",
    menuMaturity: "实验",
    pageMaturity: "实验",
  },
  {
    menuName: /考功司\s*试行/,
    path: "/evals",
    heading: "考成院",
    menuMaturity: "试行",
    pageMaturity: "Beta",
  },
  {
    menuName: /客卿馆\s*实验/,
    path: "/keqing",
    heading: "客卿",
    menuMaturity: "实验",
    pageMaturity: "实验",
  },
] as const;

test("the capability lab exposes maturity and reaches every distinctive route", async ({
  stack,
  page,
}) => {
  await installKeqingContracts(page);
  await setShellState(page, {
    theme: "dark",
    collapsed: false,
    locale: "zh-classic",
  });
  await page.goto(`${stack.baseURL}/control`);

  const sidebar = page.locator("aside");
  await sidebar.getByRole("menuitem", { name: /天工院\s*实验/ }).click();

  for (const destination of LAB_DESTINATIONS) {
    const menuItem = sidebar.getByRole("menuitem", { name: destination.menuName });
    await expect(menuItem).toBeVisible();
    await expect(menuItem.getByText(destination.menuMaturity, { exact: true })).toBeVisible();
    await menuItem.click();

    await expect(page).toHaveURL(`${stack.baseURL}${destination.path}`);
    await expect(page.getByRole("heading", { name: destination.heading })).toBeVisible();
    await expect(
      page.locator("main").getByText(destination.pageMaturity, { exact: true }).first(),
    ).toBeVisible();
  }
});
