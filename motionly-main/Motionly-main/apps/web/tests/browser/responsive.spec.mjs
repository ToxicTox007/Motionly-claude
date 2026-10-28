// The main routes fit every common width with no horizontal scrolling, and
// the navigation stays reachable (FE-24, FE-38): from the sidebar or header
// where there is room, from the Menu below that.
//
// Widths are set explicitly, so this runs in the desktop setups only
// (playwright.browser.config.mjs); they cover phone widths too, and the phone
// setups run every other spec at their own width.
import { expect, signIn, test } from "./support/fixtures.mjs";

const WIDTHS = [320, 375, 430, 768, 1024, 1280, 1440, 1920];
const HEIGHT = 900;

async function horizontalOverflow(page) {
  return page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
}

/**
 * A navigation link is reachable: visible on the page, or in the Menu
 * dialog. The menu is closed again afterwards.
 */
async function expectLinkReachable(page, name, width) {
  const direct = page
    .getByRole("link", { name, exact: true })
    .filter({ visible: true });
  if ((await direct.count()) > 0) return;
  const menuButton = page.getByRole("button", { name: "Menu", exact: true });
  await expect(menuButton, `Menu at ${width}px`).toBeVisible();
  const menu = page.getByRole("dialog", { name: "Navigation menu" });
  // A tap before hydration does nothing; retry until the menu opens.
  await expect(async () => {
    if (!(await menu.isVisible())) await menuButton.click({ timeout: 5_000 });
    await expect(menu).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 20_000 });
  await expect(
    menu.getByRole("link", { name, exact: true }),
    `${name} in the menu at ${width}px`,
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
}

async function checkWidths(page, path, navLink) {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: HEIGHT });
    await page.goto(path);
    await page.waitForLoadState("load");
    expect(new URL(page.url()).pathname, `${path} at ${width}px`).toBe(path);

    const { scrollWidth, innerWidth } = await horizontalOverflow(page);
    expect(
      scrollWidth,
      `${path} at ${width}px scrolls sideways (${scrollWidth}px of content)`,
    ).toBeLessThanOrEqual(innerWidth);
    if (navLink) await expectLinkReachable(page, navLink, width);
  }
}

for (const [path, navLink] of [
  ["/", "Log in"],
  ["/pricing", null],
  ["/login", null],
  ["/signup", null],
]) {
  test(`${path} fits 320–1920px`, async ({ page, gates }) => {
    test.setTimeout(120_000);
    await checkWidths(page, path, navLink);
    await gates.checkAccessibility(page);
  });
}

for (const [path, navLink] of [
  ["/dashboard", "Projects"],
  ["/projects", "Billing"],
  ["/billing", "Projects"],
  ["/account", "Projects"],
]) {
  test(`${path} fits 320–1920px with the navigation reachable`, async ({
    page,
    user,
    gates,
  }) => {
    test.setTimeout(120_000);
    await signIn(page, user, path);
    await checkWidths(page, path, navLink);
    await gates.checkAccessibility(page);
  });
}
