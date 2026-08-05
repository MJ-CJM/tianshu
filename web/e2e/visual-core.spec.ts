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

/**
 * 视觉基线整体过时，**当前不具备比对能力**——这是待办的产品工作，不是测试 bug。
 *
 * 证据：`__screenshots__/SHA256SUMS` 里 48 张只覆盖前一版 6 路由，而当前源码是
 * 7 路由 × 2 尺寸 × 2 主题 × 2 侧栏态 = 56 项；实测 macOS 本地 56/56 全失败。
 * docs/launch/README.md 与 capability-matrix.md 已记为「视觉终审待完成」。
 *
 * 叠加的第二个问题已顺带修掉：snapshotPathTemplate 原先不含 `{platform}`，
 * Linux CI 直接拿 macOS 基线比对，字体渲染与抗锯齿差异注定失配。现已按平台隔离，
 * 存量基线相应改名为 `-darwin`，将来各平台各自持有基线，不再跨平台误比。
 *
 * 之所以跳过而不是 `--update-snapshots` 一键重建：那等于把当前界面（可能含未审的
 * 视觉缺陷）直接固化成"正确答案"，绕过人工视觉终审，比不跑更危险。
 *
 * 跳过不等于通过——Playwright 会列出 skipped 及原因，CURRENT-STATE.md 亦有记录，
 * 不拿绿色掩盖未达成的保证。**视觉终审完成、基线按平台重建后，把此常量置 false
 * 即可恢复比对。**
 */
const AWAITING_VISUAL_SIGNOFF = true;

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
          test.skip(
            AWAITING_VISUAL_SIGNOFF,
            "视觉基线过时（48 张覆盖前一版 6 路由，现为 56 项），视觉终审待完成",
          );
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
