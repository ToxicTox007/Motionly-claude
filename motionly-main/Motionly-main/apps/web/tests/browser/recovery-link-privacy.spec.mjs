// An account-recovery link is a live credential (FE-02). It must never be in
// a URL the browser keeps or sends: not the address bar, not history, not a
// request URL or Referer. The token travels only in the fragment of the link
// (never sent to a server) and then in one POST body.
//
// Admin configuration note (as in password-reset.spec.mjs): the CPU stage
// cannot make a per-test user a platform admin (ADMIN_USER_IDS is set at
// container start), so the link is issued the way the API issues it, through
// GoTrue's service-role generate_link. The admin panel that shows the link
// (never in its URL, gone after Done or a refresh) is tested as behaviour in
// tests/components/reset-link-panel.test.tsx and
// tests/unit/reset-link-action.test.ts; here the admin pages' privacy headers
// are checked for a signed-in viewer.
import { randomBytes } from "node:crypto";
import { expect, signIn, test } from "./support/fixtures.mjs";

test("following a recovery link leaves the token nowhere the browser keeps", async ({
  page,
  stack,
  gates,
}, testInfo) => {
  const email = `privacy-${testInfo.workerIndex}-${randomBytes(6).toString("hex")}@motionly.test`;
  const created = await stack.admin.post("/supabase/auth/v1/admin/users", {
    data: {
      email,
      password: `Privacy-${randomBytes(12).toString("hex")}-Aa1!`,
      email_confirm: true,
    },
  });
  expect(created.status(), await created.text()).toBe(200);
  const { id: userId } = await created.json();

  try {
    const issued = await stack.admin.post(
      "/supabase/auth/v1/admin/generate_link",
      { data: { type: "recovery", email } },
    );
    expect(issued.status(), await issued.text()).toBe(200);
    const { hashed_token: token } = await issued.json();
    const baseURL = testInfo.project.use.baseURL.replace(/\/$/, "");
    const link = `${baseURL}/auth/confirm#token_hash=${token}&type=recovery`;

    const requests = [];
    page.on("request", (request) =>
      requests.push({
        url: request.url(),
        method: request.method(),
        referer: request.headers().referer ?? "",
        body: request.postData() ?? "",
      }),
    );

    // A page before the link, so history has an entry to go back to.
    await page.goto("/forgot-password");
    await page.goto(link);
    await page.waitForURL((url) => url.pathname === "/reset-password");
    expect(page.url()).not.toContain(token);
    await expect(page.getByLabel("New password")).toBeVisible();

    // No request carried the token in its URL or Referer; it was sent once,
    // in the body of the confirmation POST.
    for (const request of requests) {
      expect(request.url, request.url).not.toContain(token);
      expect(request.referer, request.url).not.toContain(token);
    }
    const carriers = requests.filter((request) => request.body.includes(token));
    expect(carriers.map((request) => request.method)).toEqual(["POST"]);
    expect(new URL(carriers[0].url).pathname).toBe("/app-api/auth/confirm");

    // The confirmation page replaced its own history entry: Back skips it.
    await page.goBack();
    await expect(page).toHaveURL((url) => url.pathname === "/forgot-password");
    expect(page.url()).not.toContain(token);
    await page.goForward();
    await expect(page).toHaveURL((url) => url.pathname === "/reset-password");
    expect(page.url()).not.toContain(token);
    await gates.checkAccessibility(page);
  } finally {
    await stack.admin.delete(`/supabase/auth/v1/admin/users/${userId}`);
  }
});

test("admin pages are never cached and send no Referer", async ({
  page,
  user,
}) => {
  await signIn(page, user);

  // A non-admin is sent away, but the response itself must already carry the
  // headers (the users page shows one-time recovery links to admins).
  const response = await page.request.get("/admin/users", { maxRedirects: 0 });
  const headers = response.headers();
  expect(headers["cache-control"]).toMatch(/no-store/);
  expect(headers["referrer-policy"]).toBe("no-referrer");
});
