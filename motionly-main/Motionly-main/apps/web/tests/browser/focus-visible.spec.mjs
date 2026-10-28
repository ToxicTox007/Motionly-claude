// Every control a keyboard user reaches shows where focus is (FE-10, WCAG
// 2.4.7): tabbing through a page, each focused element has an outline or a
// focus ring (a box-shadow it does not have unfocused).
//
// Runs in Chromium and Firefox on desktop (playwright.browser.config.mjs):
// WebKit's Tab skips links unless the viewer opts in, and the phone setups
// have no keyboard.
import { expect, signIn, test } from "./support/fixtures.mjs";

const TAB_STOPS = 30;

/** Tabs through the page; returns the focused controls with no indicator. */
async function controlsWithoutFocusIndicator(page) {
  // Instant transitions, so a ring is read at its final value.
  await page.emulateMedia({ reducedMotion: "reduce" });
  // Each control's unfocused look, to tell a focus ring from a resting
  // shadow (the neumorphic controls all have one).
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    const resting = new Map();
    for (const element of document.querySelectorAll("*")) {
      resting.set(element, getComputedStyle(element).boxShadow);
    }
    window.__restingShadows = resting;
  });

  const missing = [];
  const visited = [];
  for (let stop = 0; stop < TAB_STOPS; stop += 1) {
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(async () => {
      const element = document.activeElement;
      if (!(element instanceof HTMLElement) || element === document.body) {
        return null;
      }
      // Wait out any transition that is still running.
      await Promise.race([
        Promise.all(
          element
            .getAnimations()
            .filter((animation) => "transitionProperty" in animation)
            .map((animation) => animation.finished.catch(() => null)),
        ),
        new Promise((resolve) => setTimeout(resolve, 1_000)),
      ]);
      const style = getComputedStyle(element);
      const outline =
        style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0;
      const resting = window.__restingShadows.get(element) ?? "none";
      const ring = style.boxShadow !== "none" && style.boxShadow !== resting;
      const label = (
        element.getAttribute("aria-label") ||
        element.textContent ||
        element.getAttribute("name") ||
        element.getAttribute("href") ||
        ""
      )
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 60);
      return {
        control: `${element.tagName.toLowerCase()} "${label}"`,
        visible: outline || ring,
      };
    });
    if (!focused) break;
    visited.push(focused.control);
    if (!focused.visible) missing.push(focused.control);
  }
  return { missing, visited };
}

for (const path of ["/", "/login", "/signup"]) {
  test(`every control on ${path} shows focus`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState("load");

    const { missing, visited } = await controlsWithoutFocusIndicator(page);
    expect(visited.length, "Tab reached controls").toBeGreaterThan(2);
    expect(missing, "focused without a visible indicator").toEqual([]);
  });
}

test("every control on the dashboard shows focus", async ({ page, user }) => {
  await signIn(page, user);

  const { missing, visited } = await controlsWithoutFocusIndicator(page);
  expect(visited.length, "Tab reached controls").toBeGreaterThan(2);
  expect(missing, "focused without a visible indicator").toEqual([]);
});
