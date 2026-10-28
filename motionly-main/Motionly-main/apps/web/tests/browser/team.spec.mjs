// Task 21 — team screens & invite acceptance, end to end.
//
// Proves the whole invite life cycle a viewer experiences:
//   1. An owner creates an invite link on the Team page; the link is shown
//      exactly once with a copy button and carries its token in the fragment.
//   2. A brand-new, signed-out visitor follows the link, signs up through the
//      Task 11 flow, is brought back to /invite (the token survived the detour
//      in sessionStorage), joins, and lands on the inviting workspace's
//      dashboard.
//   3. Role limits apply: the new member sees a read-only Team list and no
//      Billing nav, and cannot reach billing management.
//   4. The owner removes the member; the member then loses access to the
//      workspace (its projects 403 and the dashboard no longer shows it).
//
// Written for the phase checkpoint; NOT run in this task (CONTEXT: browser
// specs run later under the heavy lock; we don't start the full CPU stack).
import { randomBytes } from "node:crypto";

import { expect, signIn, test } from "./support/fixtures.mjs";
import {
  addWorkspaceUser,
  grantAgencyPlan,
  ownedWorkspaceId,
} from "./support/workspace.mjs";

// Mirrors WORKSPACE_COOKIE_NAME in apps/web/lib/api-auth.ts, inlined so this
// spec doesn't pull the next/headers-coupled module graph into Playwright.
const WORKSPACE_COOKIE_NAME = "motionly_workspace_id";
const INVITE_TOKEN_STORAGE_KEY = "motionly_invite_token";

test("an invite link onboards a new member, who is then removed and loses access", async ({
  page,
  context,
  browser,
  user,
  stack,
  gates,
}) => {
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  await grantAgencyPlan(stack, workspaceId);

  // 1. The owner creates an invite link on the Team page.
  await signIn(page, user, "/account/team");
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByLabel("Role").selectOption("member");
  await page.getByRole("button", { name: /Create invite link/i }).click();

  const link = page.getByTestId("invite-link");
  await expect(link).toBeVisible();
  const inviteUrl = (await link.textContent())?.trim();
  expect(inviteUrl, "the invite URL carries a fragment token").toMatch(
    /\/invite#token=/,
  );
  const invitePath = inviteUrl.slice(inviteUrl.indexOf("/invite"));
  await gates.checkAccessibility(page);

  // 2. A brand-new, signed-out visitor follows the link in a fresh context.
  const invitee = {
    email: `invitee-${randomBytes(6).toString("hex")}@motionly.test`,
    password: `Invitee-${randomBytes(12).toString("hex")}-Aa1!`,
  };
  const guestContext = await browser.newContext({ ignoreHTTPSErrors: true });
  const guest = await guestContext.newPage();
  let inviteeId = null;
  try {
    await guest.goto(invitePath);
    // The token is stored so it can survive the signup detour.
    await expect
      .poll(() =>
        guest.evaluate(
          (key) => window.sessionStorage.getItem(key),
          INVITE_TOKEN_STORAGE_KEY,
        ),
      )
      .not.toBeNull();

    // Sign up through the Task 11 flow; `next` brings the guest back to /invite.
    await guest.getByRole("link", { name: "Sign up" }).click();
    await expect(guest).toHaveURL(/\/signup\?next=%2Finvite/);
    await guest.getByLabel("Full name").fill("Invited Teammate");
    await guest.getByLabel("Email").fill(invitee.email);
    await guest.getByLabel("Password").fill(invitee.password);
    await guest.getByRole("button", { name: /Create workspace/i }).click();

    // Back on /invite, now signed in: accept and land on the dashboard.
    await guest.waitForURL(/\/invite/);
    await guest.getByRole("button", { name: /Join workspace/i }).click();
    await guest.waitForURL((url) => url.pathname === "/dashboard");

    const row = await stack.admin.get(
      `/supabase/rest/v1/workspace_members?workspace_id=eq.${workspaceId}&role=eq.member&status=eq.active&select=user_id`,
    );
    const rows = await row.json();
    expect(rows.length, "the invitee is now an active member").toBe(1);
    inviteeId = String(rows[0].user_id);

    // 3. Role limits: no Billing nav, and the Team page is read-only.
    await expect(
      guest.getByRole("link", { name: "Billing", exact: true }),
    ).toHaveCount(0);
    await guest.goto("/account/team");
    await expect(
      guest.getByText(/Only the owner or an admin can/i),
    ).toBeVisible();
    // A member sees no invite form and no remove controls.
    await expect(
      guest.getByRole("button", { name: /Create invite link/i }),
    ).toHaveCount(0);
    await expect(guest.getByRole("button", { name: /^Remove/ })).toHaveCount(0);

    // 4. The owner removes the member.
    await page.goto("/account/team");
    await page.getByRole("button", { name: "Remove Invited Teammate" }).click();
    await page.getByRole("button", { name: "Remove member" }).click();
    await expect(page.getByText(/Invited Teammate was removed/i)).toBeVisible();

    // The member has lost access: the workspace projects now 403, and the
    // dashboard no longer loads that workspace.
    await guestContext.addCookies([
      { name: WORKSPACE_COOKIE_NAME, value: workspaceId, url: guest.url() },
    ]);
    const lost = await guest.request.get("/api/projects", {
      headers: { "x-motionly-workspace-id": workspaceId },
    });
    expect([401, 403]).toContain(lost.status());

    await gates.checkAccessibility(page);
  } finally {
    if (inviteeId) {
      await stack.admin.delete(`/supabase/auth/v1/admin/users/${inviteeId}`);
    }
    await guestContext.close();
  }
});

// Phase G follow-up (Task 21 UI): a member leaves the workspace (POST
// /team/leave) and loses access; the last owner is refused. Membership is
// seeded through the service-role stack (support/workspace.mjs), never guarded
// behind `if (visible)`.
test("a member leaves the workspace from the Team page and loses access", async ({
  page,
  context,
  user,
  stack,
  gates,
}) => {
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  await grantAgencyPlan(stack, workspaceId);
  // Add a second user as a member and act as them in the owner's workspace.
  const { user: member, cleanup } = await addWorkspaceUser(
    stack,
    workspaceId,
    "member",
  );

  try {
    await signIn(page, member, "/dashboard");
    await context.addCookies([
      { name: WORKSPACE_COOKIE_NAME, value: workspaceId, url: page.url() },
    ]);

    await page.goto("/account/team");
    // A member sees the Leave workspace control and confirms the dialog.
    await page.getByRole("button", { name: /Leave workspace/i }).click();
    const dialog = page.getByRole("alertdialog", { name: /Leave workspace/i });
    await expect(dialog).toBeVisible();
    await gates.checkAccessibility(page);
    await dialog.getByRole("button", { name: /Leave workspace/i }).click();

    // Leaving removes the membership: the workspace projects now 401/403 and
    // the member can no longer act in it.
    await expect
      .poll(async () => {
        const rows = await stack.admin.get(
          `/supabase/rest/v1/workspace_members?workspace_id=eq.${workspaceId}&user_id=eq.${member.id}&status=eq.active&select=user_id`,
        );
        return (await rows.json()).length;
      })
      .toBe(0);

    const lost = await page.request.get("/api/projects", {
      headers: { "x-motionly-workspace-id": workspaceId },
    });
    expect([401, 403]).toContain(lost.status());
  } finally {
    await cleanup();
  }
});

// The owner transfers ownership (POST /team/transfer-ownership, owner-only, a
// dialog naming the new owner); the new owner can then open Billing (which is
// owner-only), proving the role actually changed.
test("the owner transfers ownership and the new owner can open billing", async ({
  page,
  browser,
  user,
  stack,
  gates,
}) => {
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  await grantAgencyPlan(stack, workspaceId);
  const { user: member, cleanup } = await addWorkspaceUser(
    stack,
    workspaceId,
    "member",
  );

  try {
    // The owner transfers ownership to the member via the Team page dialog.
    await signIn(page, user, "/account/team");
    await page
      .getByRole("button", { name: /Transfer ownership to/i })
      .first()
      .click();
    const dialog = page.getByRole("alertdialog", {
      name: /Transfer ownership/i,
    });
    await expect(dialog).toBeVisible();
    // The dialog names the member becoming the owner.
    await expect(
      dialog.getByText(/will become the workspace owner/i),
    ).toBeVisible();
    await gates.checkAccessibility(page);
    await dialog.getByRole("button", { name: /Make .* owner/i }).click();

    // The role change lands: the member is now the workspace owner.
    await expect
      .poll(async () => {
        const rows = await stack.admin.get(
          `/supabase/rest/v1/workspace_members?workspace_id=eq.${workspaceId}&user_id=eq.${member.id}&select=role`,
        );
        return (await rows.json())[0]?.role;
      })
      .toBe("owner");

    // The new owner signs in, selects the workspace, and can open Billing — an
    // owner-only page — without being shown the owner-only lockout banner.
    const newOwnerContext = await browser.newContext({
      ignoreHTTPSErrors: true,
    });
    const newOwner = await newOwnerContext.newPage();
    try {
      await signIn(newOwner, member, "/dashboard");
      await newOwnerContext.addCookies([
        {
          name: WORKSPACE_COOKIE_NAME,
          value: workspaceId,
          url: newOwner.url(),
        },
      ]);
      const response = await newOwner.goto("/billing");
      expect(response?.status()).toBeLessThan(400);
      await expect(
        newOwner.getByText("Current plan", { exact: true }),
      ).toBeVisible();
      await expect(
        newOwner.getByText("Only the owner can manage billing"),
      ).toHaveCount(0);
    } finally {
      await newOwnerContext.close();
    }
  } finally {
    await cleanup();
  }
});
