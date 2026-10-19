// Signing in and out through the real forms, over HTTPS at the edge.
import { expect, signIn, test } from "./support/fixtures.mjs";

const PROTECTED_PAGES = [
  "/dashboard",
  "/projects",
  "/projects/new",
  "/billing",
  "/account",
  "/admin/ops",
  "/admin/editorial-learning",
];

for (const path of PROTECTED_PAGES) {
  test(`signed-out ${path} redirects to the login page`, async ({ page }) => {
    await page.goto(path);

    await expect(page).toHaveURL((url) => url.pathname === "/login");
    expect(new URL(page.url()).searchParams.get("next")).toBe(path);
    await expect(page.getByRole("button", { name: "Log in" })).toBeVisible();
  });
}

// B14: the login page's return address must stay on this site. Browsers read
// "/\\evil.example" as "//evil.example", another site.
test("after signing in, a return address on another site is ignored", async ({
  page,
  context,
  user,
  baseURL,
}) => {
  for (const next of [
    "/\\evil.example",
    "//evil.example",
    "https://evil.example/dashboard",
  ]) {
    await context.clearCookies();
    await page.goto(`/login?next=${encodeURIComponent(next)}`);
    await page.getByLabel("Email").fill(user.email);
    await page.getByLabel("Password").fill(user.password);
    await page.getByRole("button", { name: "Log in" }).click();
    await page.waitForURL((url) => url.pathname === "/dashboard");
    expect(new URL(page.url()).origin, next).toBe(new URL(baseURL).origin);
  }
});

test("a wrong password keeps the viewer on the login page with an error", async ({
  page,
  user,
}) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(user.email);
  await page.getByLabel("Password").fill(`${user.password}-wrong`);
  await page.getByRole("button", { name: "Log in" }).click();

  await expect(page).toHaveURL(/\/login\?error=invalid_credentials/);
  await expect(
    page.getByText("Invalid email or password. Please try again."),
  ).toBeVisible();
});

test("signing in returns to the requested page and the session uses one secure cookie", async ({
  page,
  context,
  user,
}) => {
  await signIn(page, user, "/projects");

  await expect(page).toHaveURL(/\/projects$/);
  const cookies = (await context.cookies()).filter((cookie) =>
    cookie.name.startsWith("sb-motionly-auth-token"),
  );
  expect(cookies.length).toBeGreaterThan(0);
  for (const cookie of cookies) {
    expect(cookie.secure).toBe(true);
    expect(cookie.sameSite).toBe("Lax");
  }
});

test("logging out ends the session", async ({ page, user }) => {
  await signIn(page, user, "/account");

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).not.toHaveURL(/\/account$/);
  await page.goto("/dashboard");
  await expect(page).toHaveURL((url) => url.pathname === "/login");
});

// Password managers (notably in Safari and iOS) and typing before the page
// hydrates set field values without input events.
async function fillLikeAutofill(page, values) {
  await page.evaluate((entries) => {
    for (const [name, value] of entries) {
      document.querySelector(`[name="${name}"]`).value = value;
    }
  }, Object.entries(values));
}

test("signing in works when the browser fills the form without input events", async ({
  page,
  user,
}) => {
  await page.goto("/login?next=%2Fdashboard");
  await page.getByLabel("Password").waitFor();
  await page.waitForLoadState("networkidle");
  await fillLikeAutofill(page, { email: user.email, password: user.password });
  await page.getByRole("button", { name: "Log in" }).click();

  await page.waitForURL((url) => url.pathname === "/dashboard");
});
