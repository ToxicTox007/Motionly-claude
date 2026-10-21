// Task 11: sign-up without email. A visitor signs up and starts right away;
// Auth's own sign-up endpoint is disabled, so the rate-limited API route is
// the only way to create an account.
import { randomBytes } from "node:crypto";
import { accessTokenClaims, expect, test } from "./support/fixtures.mjs";
import { stackCredentials } from "./support/stack.mjs";

const freshEmail = (testInfo) =>
  `signup-${testInfo.workerIndex}-${randomBytes(6).toString("hex")}@motionly.test`;
const strongPassword = () => `Signup-${randomBytes(12).toString("hex")}-Aa1!`;

async function fillSignup(page, { name, email, password }) {
  await page.goto("/signup");
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create workspace" }).click();
}

test("a new visitor signs up and lands on a dashboard with a workspace", async ({
  page,
  context,
  stack,
  gates,
}, testInfo) => {
  const name = `Asha ${randomBytes(3).toString("hex")}`;
  const email = freshEmail(testInfo);
  let userId;
  try {
    await fillSignup(page, { name, email, password: strongPassword() });

    await page.waitForURL((url) => url.pathname === "/dashboard");
    userId = (await accessTokenClaims(context)).sub;
    await expect(page.getByTestId("workspace-name")).toHaveText(
      `${name}'s workspace`,
    );
    await expect(
      page.getByRole("link", { name: /create new video/i }).first(),
    ).toBeVisible();
    await gates.checkAccessibility(page);

    // The account was created already confirmed: no email is involved.
    const account = await stack.admin.get(
      `/supabase/auth/v1/admin/users/${userId}`,
    );
    expect(account.status()).toBe(200);
    const user = await account.json();
    expect(user.email).toBe(email);
    expect(user.email_confirmed_at).toBeTruthy();
  } finally {
    if (userId) {
      const deleted = await stack.admin.delete(
        `/supabase/auth/v1/admin/users/${userId}`,
      );
      expect(deleted.status(), await deleted.text()).toBe(200);
    }
  }
});

test("signing up with an email that has an account points to login", async ({
  page,
  user,
  gates,
}) => {
  await fillSignup(page, {
    name: "Someone Else",
    email: user.email,
    password: strongPassword(),
  });

  await expect(page).toHaveURL(/\/signup\?error=email_taken$/);
  await expect(
    page.getByText(
      "An account with this email already exists. Log in instead.",
    ),
  ).toBeVisible();
  await gates.checkAccessibility(page);
});

test("a common password is refused", async ({ page, gates }, testInfo) => {
  await fillSignup(page, {
    name: "Weak Password",
    email: freshEmail(testInfo),
    password: "password",
  });

  await expect(page).toHaveURL(/\/signup\?error=password_weak$/);
  await expect(page.getByText(/Choose a stronger password/)).toBeVisible();
  await gates.checkAccessibility(page);
});

test("Auth's own sign-up endpoint is refused and creates no account", async ({
  request,
}, testInfo) => {
  const { anonKey } = stackCredentials();
  const email = freshEmail(testInfo);
  const password = strongPassword();
  const headers = { apikey: anonKey, authorization: `Bearer ${anonKey}` };

  const signup = await request.post("/supabase/auth/v1/signup", {
    headers,
    data: { email, password },
  });
  expect([403, 422]).toContain(signup.status());
  expect((await signup.json()).error_code).toBe("signup_disabled");

  const signIn = await request.post(
    "/supabase/auth/v1/token?grant_type=password",
    { headers, data: { email, password } },
  );
  expect(signIn.status()).toBe(400);
  expect((await signIn.json()).error_code).toBe("invalid_credentials");
});
