// Task 12: admin-assisted password reset, end to end. A user who forgot their
// password requests help, an admin issues a one-time recovery link, the user
// resets through it, the old password stops working, the new one logs in, and
// a session opened before the reset is signed out.
//
// Admin configuration note: the CPU stage cannot mark a per-test user as an
// admin (ADMIN_USER_IDS is set at container start, before this test's users
// exist). So this spec issues the recovery link the same way the API does —
// through GoTrue's service-role admin generate_link — using the stack's
// service-role request context (stack.admin). The admin UI and the
// /admin/users API routes are covered by apps/api/tests/test_password_reset.py
// and apps/web/tests/password-reset.test.mjs. If the CPU stage later gains a
// way to promote a user to admin for a test, swap the generate_link call for a
// POST /api/admin/users/reset-links with x-motionly-admin-token.
import { randomBytes } from "node:crypto";
import { expect, signIn, test } from "./support/fixtures.mjs";

const strongPassword = () => `Reset-${randomBytes(12).toString("hex")}-Aa1!`;

async function issueRecoveryLink(stackAdmin, baseURL, email) {
  const response = await stackAdmin.post(
    "/supabase/auth/v1/admin/generate_link",
    { data: { type: "recovery", email } },
  );
  expect(response.status(), await response.text()).toBe(200);
  const { hashed_token: tokenHash } = await response.json();
  // The link the admin would hand out: token in the fragment, not the query.
  return `${baseURL}/auth/confirm#token_hash=${tokenHash}&type=recovery`;
}

test("a forgotten password is reset through an admin link and old sessions end", async ({
  page,
  context,
  browser,
  stack,
  gates,
}, testInfo) => {
  const email = `reset-${testInfo.workerIndex}-${randomBytes(6).toString("hex")}@motionly.test`;
  const oldPassword = strongPassword();
  const newPassword = strongPassword();

  // Create a confirmed user with a known password (not the shared `user`
  // fixture, so we control the password for the "old password fails" check).
  const created = await stack.admin.post("/supabase/auth/v1/admin/users", {
    data: { email, password: oldPassword, email_confirm: true },
  });
  expect(created.status(), await created.text()).toBe(200);
  const { id: userId } = await created.json();

  try {
    // A second browser context: a session opened before the reset. It must be
    // signed out of its OTHER-session once the password is reset.
    const otherContext = await browser.newContext({ ignoreHTTPSErrors: true });
    const otherPage = await otherContext.newPage();
    await signIn(otherPage, { email, password: oldPassword });
    await expect(otherPage).toHaveURL((url) => url.pathname === "/dashboard");

    // 1. The user asks for help on the public forgot-password page.
    await page.goto("/forgot-password");
    await page.getByLabel("Email").fill(email);
    await page.getByRole("button", { name: "Request reset" }).click();
    await expect(page.getByText(/an admin will set up/i)).toBeVisible();

    // 2. The admin issues a one-time recovery link.
    const baseURL = testInfo.project.use.baseURL.replace(/\/$/, "");
    const link = await issueRecoveryLink(stack.admin, baseURL, email);

    // 3. The user follows the link: /auth/confirm reads the fragment, clears
    //    it, and POSTs the token to the server, which verifies it and sets the
    //    recovery session cookie; the page then sends them to /reset-password,
    //    where they choose a new password.
    await page.goto(link);
    await page.waitForURL((url) => url.pathname === "/reset-password");
    await page.getByLabel("New password").fill(newPassword);
    await page.getByRole("button", { name: "Set new password" }).click();
    await page.waitForURL((url) => url.pathname === "/dashboard");

    // 4. The old password no longer works.
    const oldLogin = await stack.admin.post(
      "/supabase/auth/v1/token?grant_type=password",
      { data: { email, password: oldPassword } },
    );
    expect(oldLogin.status()).toBe(400);

    // 5. The new password logs in.
    await context.clearCookies();
    await signIn(page, { email, password: newPassword });
    await expect(page).toHaveURL((url) => url.pathname === "/dashboard");

    // 6. The session opened before the reset has been signed out of its other
    //    session: its refresh token is dead, so a guarded page bounces it to
    //    login.
    await otherPage.goto("/dashboard");
    await otherPage.waitForURL((url) => url.pathname === "/login");

    await gates.checkAccessibility(page);
    await otherContext.close();
  } finally {
    await stack.admin.delete(`/supabase/auth/v1/admin/users/${userId}`);
  }
});

test("an expired or reused reset link shows a clear error, never a crash", async ({
  page,
  stack,
  gates,
}, testInfo) => {
  const email = `reset-reuse-${testInfo.workerIndex}-${randomBytes(6).toString("hex")}@motionly.test`;
  const created = await stack.admin.post("/supabase/auth/v1/admin/users", {
    data: { email, password: strongPassword(), email_confirm: true },
  });
  expect(created.status(), await created.text()).toBe(200);
  const { id: userId } = await created.json();

  try {
    const baseURL = testInfo.project.use.baseURL.replace(/\/$/, "");
    const link = await issueRecoveryLink(stack.admin, baseURL, email);

    // Use the link once.
    await page.goto(link);
    await page.waitForURL((url) => url.pathname === "/reset-password");

    // The second use of the same single-use token is rejected with a clear
    // error page, not a 500. The CHECK runs server-side now: the page POSTs the
    // token to /app-api/auth/confirm, which verifies it and answers 400 with a
    // reason. The browser may log that 400 as a failed resource load; allow it.
    gates.allowConsoleError(/app-api\/auth\/confirm|400/i);
    await page.goto(link);
    await expect(page.getByText(/reset link problem/i)).toBeVisible();
    await expect(
      page.getByText(/expired or has already been used/i),
    ).toBeVisible();
    await gates.checkAccessibility(page);
  } finally {
    await stack.admin.delete(`/supabase/auth/v1/admin/users/${userId}`);
  }
});
