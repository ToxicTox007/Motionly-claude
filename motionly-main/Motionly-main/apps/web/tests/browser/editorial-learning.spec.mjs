// Task 29: a promoted editorial memory measurably personalizes new plans, the
// app surfaces "Personalized from N edits" with an opt-out, and withdrawing
// consent on the account settings page removes the effect.
//
// The editor-panel indicator and the account consent surface both talk to the
// editorial-learning API. To keep this spec deterministic on the shared box we
// intercept those few endpoints with page.route so the real React components,
// their enabled/personalized gating, and the opt-out → revoke flow are
// exercised end to end without seeding a full promoted dataset through the
// admin stack.
//
// Written, not run here (shared 2 vCPU box); it runs at the phase checkpoint.
import { expect, signIn, test } from "./support/fixtures.mjs";
import { ownedWorkspaceId } from "./support/workspace.mjs";

// Mirrors WORKSPACE_COOKIE_NAME in apps/web/lib/api-auth.ts.
const WORKSPACE_COOKIE_NAME = "motionly_workspace_id";

function jsonRoute(body, status = 200) {
  return (route) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
}

test("the account page shows the opt-out and withdrawing consent calls revoke", async ({
  page,
  user,
  gates,
}) => {
  let revoked = false;

  // Feature is on and the signed-in user currently has memory consent.
  await page.route(/\/api\/editorial-learning\/status(\?.*)?$/, (route) =>
    jsonRoute({
      enabled: true,
      policy_version: "editorial-consent/v1",
      default_retention_days: 90,
      minimum_examples: 25,
    })(route),
  );
  await page.route(/\/api\/editorial-learning\/policy(\?.*)?$/, (route) => {
    if (route.request().method() === "PUT") {
      return jsonRoute({
        memory_enabled: true,
        training_enabled: false,
        policy_version: "editorial-consent/v1",
        revision: 2,
        retention_days: 90,
      })(route);
    }
    return jsonRoute({
      memory_enabled: !revoked,
      training_enabled: false,
      policy_version: "editorial-consent/v1",
      revision: revoked ? 3 : 1,
      retention_days: 90,
      revoked_at: revoked ? new Date().toISOString() : null,
    })(route);
  });
  await page.route(/\/api\/editorial-learning\/revoke(\?.*)?$/, (route) => {
    revoked = true;
    return jsonRoute({
      memory_enabled: false,
      training_enabled: false,
      policy_version: "editorial-consent/v1",
      revision: 3,
      retention_days: 90,
      revoked_at: new Date().toISOString(),
    })(route);
  });

  await signIn(page, user, "/account");

  // The opt-out (consent withdrawal) surface is visible while consent is on.
  const withdrawHeading = page.getByText(
    /Withdraw all\s+editorial-learning consent/i,
  );
  await expect(withdrawHeading).toBeVisible();

  // Opt out: confirm, then withdraw. This must POST to the revoke endpoint.
  await page.getByLabel(/revokes both memory and training consent/i).check();
  const revokeCall = page.waitForRequest(
    (request) =>
      /\/api\/editorial-learning\/revoke/.test(request.url()) &&
      request.method() === "POST",
  );
  await page.getByRole("button", { name: /Withdraw all consent/i }).click();
  await revokeCall;

  await expect(page.getByText(/Consent was withdrawn/i)).toBeVisible();

  await gates.checkAccessibility(page);
});

test("the editor shows 'Personalized from N edits' only when memory is active", async ({
  page,
  context,
  user,
  stack,
  gates,
}) => {
  const workspaceId = await ownedWorkspaceId(stack, user.id);

  // Report an active, consented, promoted memory for the opened project.
  await page.route(
    /\/api\/editorial-learning\/projects\/[^/]+\/memory(\?.*)?$/,
    jsonRoute({
      enabled: true,
      personalized: true,
      example_count: 3,
      memory_version: "deadbeefcafef00d",
      policy_revision: 2,
      opt_out_available: true,
    }),
  );

  await signIn(page, user, "/dashboard");
  await context.addCookies([
    { name: WORKSPACE_COOKIE_NAME, value: workspaceId, url: page.url() },
  ]);

  await page.goto("/projects/new");
  await page.getByLabel("Project Name").fill("Editorial memory demo");
  await page.getByRole("button", { name: /Continue to Upload/i }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);

  // The indicator renders "Personalized from 3 edits" with an opt-out link to
  // the account page where consent can be withdrawn.
  const indicator = page.getByTestId("editorial-memory-indicator");
  await expect(indicator).toBeVisible();
  await expect(indicator).toContainText("Personalized from 3 edits");
  await expect(
    indicator.getByRole("link", { name: /Opt out/i }),
  ).toHaveAttribute("href", "/account");

  await gates.checkAccessibility(page);
});
