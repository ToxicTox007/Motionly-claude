// Every public page renders for a signed-out viewer and passes the gates.
import { expect, navigate, test } from "./support/fixtures.mjs";

// Each page's title names it (WCAG 2.4.2).
const PUBLIC_PAGES = {
  "/": "Motionly",
  "/login": "Log in · Motionly",
  "/signup": "Sign up · Motionly",
  "/pricing": "Pricing · Motionly",
  "/privacy": "Privacy Policy · Motionly",
  "/terms": "Terms of Service · Motionly",
  "/refund": "Refund Policy · Motionly",
  "/copyright": "Copyright Policy · Motionly",
};

for (const [path, title] of Object.entries(PUBLIC_PAGES)) {
  test(`public page ${path} renders`, async ({ page, gates }) => {
    const response = await page.goto(path);

    expect(response?.status()).toBe(200);
    await expect(page).toHaveTitle(title);
    await expect(page.locator("h1").first()).toBeVisible();
    await gates.checkAccessibility(page);
  });
}

test("an unknown page answers 404 with a page", async ({ page, gates }) => {
  // Chromium and WebKit log the 404 document as a console error.
  gates.allowConsoleError(/status of 404/);

  const response = await page.goto("/no-such-page");

  expect(response?.status()).toBe(404);
  await expect(page.getByText(/could not be found/i).first()).toBeVisible();
});

test("the landing page leads to sign-up and sign-in", async ({ page }) => {
  await page.goto("/");

  await page
    .getByRole("link", { name: /start free/i })
    .filter({ visible: true })
    .first()
    .click();
  await expect(page).toHaveURL(/\/signup$/);
  await page.goto("/");
  await navigate(page, "Log in");
  await expect(page).toHaveURL(/\/login$/);
});
