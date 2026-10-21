// Task 10 (N4): a session outlives its access token. The CPU stage issues
// short-lived tokens (GOTRUE_JWT_EXP in infra/docker-compose.cpu-stage.yml),
// so a page left open past the token's expiry must keep working: browser API
// calls use the renewed token, and page loads renew the session on the server.
import {
  accessTokenClaims,
  expect,
  signIn,
  test,
} from "./support/fixtures.mjs";
import { createSourceVideo } from "./support/media.mjs";

let media;

test.beforeAll(() => {
  media = createSourceVideo();
});

test.afterAll(() => media?.remove());

test("an upload started after the page's access token expired still succeeds", async ({
  page,
  context,
  user,
  gates,
}) => {
  test.setTimeout(330_000);
  const apiStatuses = [];
  const renewals = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/")) {
      apiStatuses.push(
        `${response.status()} ${response.request().method()} ${url.pathname}`,
      );
    }
    if (
      url.pathname === "/supabase/auth/v1/token" &&
      url.searchParams.get("grant_type") === "refresh_token"
    ) {
      renewals.push(response.status());
    }
  });

  await signIn(page, user, "/projects/new");
  await page.getByLabel("Project Name").fill("Session outlives its token");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await expect(
    page.getByRole("heading", { name: "Upload your footage" }),
  ).toBeVisible();

  // The page was rendered with this token; wait, without reloading, until it
  // has expired.
  const claims = await accessTokenClaims(context);
  expect(
    claims.exp - claims.iat,
    "the CPU stage issues short-lived tokens",
  ).toBeLessThanOrEqual(300);
  const waitMs = claims.exp * 1000 + 5_000 - Date.now();
  expect(waitMs).toBeLessThan(300_000);
  await page.waitForTimeout(Math.max(0, waitMs));

  await page
    .getByLabel("Choose a video file to upload")
    .setInputFiles(media.path);
  await page.getByRole("button", { name: /^Upload$/ }).click();
  await expect
    .poll(() => apiStatuses, { timeout: 30_000 })
    .toEqual(
      expect.arrayContaining([
        expect.stringMatching(
          /^201 POST \/api\/projects\/[^/]+\/uploads\/multipart\/init$/,
        ),
        expect.stringMatching(
          /^200 POST \/api\/projects\/[^/]+\/uploads\/multipart\/[^/]+\/complete$/,
        ),
      ]),
    );
  expect(apiStatuses.filter((entry) => /^401 /.test(entry))).toEqual([]);
  expect(renewals.length, "the browser renewed its token").toBeGreaterThan(0);
  expect(renewals.filter((status) => status !== 200)).toEqual([]);

  // A page load after expiry renews the session on the server, too.
  await page.goto("/projects");
  await expect(page).toHaveURL((url) => url.pathname === "/projects");
  await expect(page).toHaveTitle("Projects · Motionly");
  await gates.checkAccessibility(page);
});
