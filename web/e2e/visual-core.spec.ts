import type { Page } from "@playwright/test";
import { palettes } from "../src/theme/palette";

import {
  CORE_ROUTES,
  THEMES,
  VIEWPORTS,
  prepareCoreRoute,
  setShellState,
  test,
} from "./fixtures";

const LAB_ROUTES = new Set(["evolution", "universes", "evals", "keqing"]);

function volatileMasks(page: Page, route: string) {
  if (route === "control") {
    return [page.getByText(/^(?:Last updated|最近更新)/)] as const;
  }
  if (route === "edict-detail") {
    return [
      page.locator("main code"),
      page.locator('main span[title^="01"]'),
      page.getByText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/),
      page.locator("main .ant-timeline-item-label"),
    ] as const;
  }
  if (route === "workbench") {
    return [
      page.locator("main .ant-table-tbody tr").first().locator("td").nth(1),
      page.getByText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/),
    ] as const;
  }
  return [] as const;
}

for (const route of CORE_ROUTES) {
  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      for (const collapsed of [false, true]) {
        test(`${route.name} ${viewport.name} ${theme} ${collapsed ? "collapsed" : "expanded"}`, async ({
          visualStack: stack,
          page,
        }) => {
          await page.setViewportSize(viewport.size);
          await setShellState(page, { theme, collapsed, locale: "zh-classic" });
          await prepareCoreRoute(page, stack, route);
          await page.locator("main").first().waitFor();
          if (!collapsed && LAB_ROUTES.has(route.name)) {
            const sidebar = page.locator("aside");
            await test.expect(
              sidebar.getByRole("menuitem", { name: /天工院\s*实验/ }),
            ).toHaveAttribute("aria-expanded", "true");
          }
          await test.expect(page).toHaveScreenshot(
            `${route.name}-${viewport.name}-${theme}-${collapsed ? "collapsed" : "expanded"}.png`,
            {
              animations: "disabled",
              fullPage: true,
              mask: [...volatileMasks(page, route.name)],
              maskColor: palettes[theme].bgContainer,
            },
          );
        });
      }
    }
  }
}
