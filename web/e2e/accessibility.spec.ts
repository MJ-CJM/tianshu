import {
  CORE_ROUTES,
  assertAxeClean,
  assertKeyboardActionsHaveVisibleFocus,
  assertZoomHasNoPrimaryHorizontalTrap,
  prepareCoreRoute,
  test,
} from "./fixtures";

for (const route of CORE_ROUTES) {
  test(`${route.name} passes axe serious/critical`, async ({ stack, page }) => {
    await prepareCoreRoute(page, stack, route);
    await assertAxeClean(page);
  });

  test(`${route.name} exposes every action to visible keyboard focus`, async ({ stack, page }) => {
    await prepareCoreRoute(page, stack, route);
    await assertKeyboardActionsHaveVisibleFocus(page);
  });

  test(`${route.name} keeps shell and primary content operable at 200% zoom`, async ({
    stack,
    page,
  }) => {
    await prepareCoreRoute(page, stack, route);
    await assertZoomHasNoPrimaryHorizontalTrap(page);
  });
}
