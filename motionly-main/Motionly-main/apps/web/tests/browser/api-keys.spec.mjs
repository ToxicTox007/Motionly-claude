// Task 24 — Agency-plan API keys.
//
// Two things are proven here:
//   1. The settings UI gates on plan + role, shows a newly-created key exactly
//      once (with a copy button), and lists keys by prefix/last-4 only.
//   2. An `mtly_` key drives the documented automation flow through the single
//      public origin / CDN proxy: create a project, upload (resumable), then
//      revocation takes effect immediately (401).
//
// Written for the phase checkpoint; NOT run in this task (CONTEXT: browser
// specs run later under the heavy lock, and we don't start the full CPU stack).
// The agency-plan setup uses the service-role `stack.admin` context to upgrade
// the test workspace's subscription, mirroring scripts/api-keys/smoke.mjs.
import { randomBytes } from "node:crypto";
import { expect, signIn, test } from "./support/fixtures.mjs";
import { grantAgencyPlan, ownedWorkspaceId } from "./support/workspace.mjs";

// The test user owns its workspace (handle_new_user); resolve it by ownership.
const workspaceIdFor = (stack, userId) => ownedWorkspaceId(stack, userId);

test("non-agency workspaces see the upgrade gate, not the key manager", async ({
  page,
  user,
  gates,
}) => {
  await signIn(page, user, "/account/api-keys");
  await expect(
    page.getByText(/API access is an Agency-plan feature/i),
  ).toBeVisible();
  // No create form for a plan without api_access.
  await expect(page.getByLabel(/Create API key/i)).toHaveCount(0);
  await gates.checkAccessibility(page);
});

test("an agency owner creates a key shown once and can copy it", async ({
  page,
  context,
  user,
  stack,
  gates,
}) => {
  const workspaceId = await workspaceIdFor(stack, user.id);
  await grantAgencyPlan(stack, workspaceId);

  await signIn(page, user, "/account/api-keys");
  await page.getByLabel(/Create API key/i).fill("CI pipeline");
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: /Create key/i }).click();

  // The raw secret is shown exactly once, with a copy button.
  const secret = page.getByTestId("api-key-secret");
  await expect(secret).toBeVisible();
  const value = (await secret.textContent())?.trim();
  expect(value?.startsWith("mtly_")).toBeTruthy();
  await page.getByRole("button", { name: /^Copy$/ }).click();
  await expect(page.getByRole("button", { name: /Copied/ })).toBeVisible();

  // The list shows the key by prefix/last-4 only, never the secret again.
  await expect(page.getByRole("cell", { name: "CI pipeline" })).toBeVisible();
  await expect(page.getByText(value)).toHaveCount(1); // only the one-time reveal

  await gates.checkAccessibility(page);

  // Reloading loses the one-time secret (it is never re-fetched).
  await page.reload();
  await expect(page.getByTestId("api-key-secret")).toHaveCount(0);
});

test("a key creates a project, uploads, then revocation returns 401", async ({
  page,
  request,
  user,
  stack,
}) => {
  const workspaceId = await workspaceIdFor(stack, user.id);
  await grantAgencyPlan(stack, workspaceId);

  // Create a key through the UI and capture the one-time secret.
  await signIn(page, user, "/account/api-keys");
  await page
    .getByLabel(/Create API key/i)
    .fill(`e2e-${randomBytes(3).toString("hex")}`);
  await page.getByRole("button", { name: /Create key/i }).click();
  const apiKey = (
    await page.getByTestId("api-key-secret").textContent()
  )?.trim();
  expect(apiKey?.startsWith("mtly_")).toBeTruthy();

  const keyHeaders = {
    authorization: `Bearer ${apiKey}`,
    "content-type": "application/json",
    "x-motionly-workspace-id": workspaceId,
  };

  // The KEY creates a project through the /api origin (the CDN proxy).
  const projectRes = await request.post("/api/projects", {
    headers: keyHeaders,
    data: { title: "API key e2e" },
  });
  expect(projectRes.status(), await projectRes.text()).toBe(201);
  const project = await projectRes.json();

  // A route NOT on the allow-list is refused even with a valid key.
  const refused = await request.get("/api/billing/status", {
    headers: keyHeaders,
  });
  expect(refused.status()).toBe(403);
  expect((await refused.json()).error.code).toBe("api_key_route_forbidden");

  // The KEY initialises a resumable upload (bytes flow via the signed URL /
  // CDN proxy, exercised fully by scripts/api-keys/smoke.mjs).
  const initRes = await request.post(
    `/api/projects/${project.id}/uploads/multipart/init`,
    {
      headers: keyHeaders,
      data: {
        file_name: "e2e.mp4",
        content_type: "video/mp4",
        total_size: 1024,
        part_size: 1024,
      },
    },
  );
  expect([200, 201]).toContain(initRes.status());

  // Revoke the key via the UI, then prove it stops working immediately.
  await page
    .getByRole("button", { name: /Revoke API key/i })
    .first()
    .click();
  await expect(page.getByText(/Revoked/).first()).toBeVisible();

  const afterRes = await request.get("/api/projects", { headers: keyHeaders });
  expect(afterRes.status()).toBe(401);
  expect((await afterRes.json()).error.code).toBe("api_key_invalid");
});
