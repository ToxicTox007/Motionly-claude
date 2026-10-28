// Web source-contract tests for the team screens (Task 21). Following the
// project's convention for server/client components, these assert the pieces
// that matter without a DOM: owner/admin gating, the one-time copyable invite
// link, seat usage + the owner-vs-admin upgrade prompt, dialog focus
// management, announced status, the /invite token-in-fragment + sessionStorage
// detour, the login/signup `next` wiring, and the app-shell Team nav + picker.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const source = (path) => readFile(new URL(path, root), "utf8");

test("the Team page loads members + invites and gates management", async () => {
  const page = await source("app/(app)/account/team/page.tsx");
  // Members come from workspace_members (no list-members endpoint exists).
  assert.match(page, /from\("workspace_members"\)/);
  // Invitations come from the API, only for owners/admins.
  assert.match(page, /\/team\/invitations/);
  assert.match(page, /canManageWorkspace/);
  // Seat usage uses the pure helper and the plan's seats entitlement.
  assert.match(page, /seatUsage/);
  assert.match(page, /status\.entitlements\.seats/);
  // Members get a read-only view.
  assert.match(page, /Only the owner or an admin can/);
});

test("the invite link is shown once with a copy button and announced", async () => {
  const client = await source("app/(app)/account/team/TeamClient.tsx");
  assert.match(client, /data-testid="invite-link"/);
  assert.match(client, /navigator\.clipboard\.writeText\(url\)/);
  assert.match(client, /only time the link is shown/i);
  assert.match(client, /aria-live="polite"/);
  // The revealed link lives only in component state.
  assert.match(client, /revealedUrl/);
});

test("destructive member/invite actions use a focus-trapped dialog", async () => {
  const client = await source("app/(app)/account/team/TeamClient.tsx");
  assert.match(client, /useFocusTrap\(true, onClose\)/);
  assert.match(client, /role="alertdialog"/);
  assert.match(client, /aria-modal="true"/);
  assert.match(client, /aria-labelledby="confirm-title"/);
  // Remove and revoke both go through the confirm dialog.
  assert.match(client, /confirmLabel: "Remove member"/);
  assert.match(client, /confirmLabel: "Revoke invite"/);
});

test("the Team UI announces status and labels every control", async () => {
  const client = await source("app/(app)/account/team/TeamClient.tsx");
  assert.match(client, /role="status"/);
  assert.match(client, /role="alert"/);
  // Role select and destructive buttons are labelled.
  assert.match(client, /aria-label=\{`Remove \$\{member\.name\}`\}/);
  assert.match(client, /aria-label=\{`Revoke \$\{invite\.role\} invite`\}/);
  assert.match(
    client,
    /<caption className="sr-only">Workspace members<\/caption>/,
  );
  // The seat usage region shows used/limit.
  assert.match(client, /data-testid="seat-usage"/);
});

test("the upgrade prompt links owners to billing; admins are told to ask", async () => {
  const client = await source("app/(app)/account/team/TeamClient.tsx");
  assert.match(client, /upgradePrompt\(viewerRole\)/);
  assert.match(client, /prompt\.actionHref/);
  assert.match(client, /usage\?\.atLimit/);
});

test("server actions enforce owner/admin and only reveal the token once", async () => {
  const actions = await source("app/(app)/account/team/actions.ts");
  assert.match(actions, /"use server"/);
  assert.match(actions, /canManageWorkspace\(context\.role\)/);
  assert.match(actions, /workspace_role_forbidden/);
  assert.match(actions, /\/team\/invitations/);
  assert.match(
    actions,
    /\/team\/members\/\$\{encodeURIComponent\(userId\)\}\/role/,
  );
  assert.match(
    actions,
    /ok: true; invitation: Invitation; url: string; token: string/,
  );
});

test("the invite page reads the token from the fragment and POSTs it", async () => {
  const client = await source("app/invite/InviteClient.tsx");
  assert.match(client, /inviteTokenFromHash\(window\.location\.hash\)/);
  // The token survives the login/signup detour in sessionStorage.
  assert.match(client, /sessionStorage\.getItem\(INVITE_TOKEN_STORAGE_KEY\)/);
  assert.match(client, /sessionStorage\.setItem\(INVITE_TOKEN_STORAGE_KEY/);
  // Signed-out visitors go to login/signup with a safe next back to /invite.
  assert.match(client, /\/login\?next=\$\{encodeURIComponent\(INVITE_NEXT\)\}/);
  assert.match(
    client,
    /\/signup\?next=\$\{encodeURIComponent\(INVITE_NEXT\)\}/,
  );
  // On success it clears the token and lands on the dashboard.
  assert.match(
    client,
    /sessionStorage\.removeItem\(INVITE_TOKEN_STORAGE_KEY\)/,
  );
  assert.match(client, /router\.replace\("\/dashboard"\)/);
});

test("the invite accept action requires sign-in, POSTs the token in a body, pins the workspace", async () => {
  const actions = await source("app/invite/actions.ts");
  assert.match(actions, /"use server"/);
  assert.match(actions, /not_signed_in/);
  assert.match(actions, /\/team\/invitations\/accept/);
  assert.match(actions, /body: JSON\.stringify\(\{ token: trimmed \}\)/);
  assert.match(actions, /WORKSPACE_COOKIE_NAME/);
});

test("signup carries a safe next through the login/signup flow", async () => {
  const actions = await source("app/(auth)/actions.ts");
  assert.match(actions, /safeNextPath\(formData\.get\("next"\), env\.appUrl\)/);
  assert.match(actions, /redirectTo = nextPath/);
  const signupPage = await source("app/(auth)/signup/page.tsx");
  assert.match(
    signupPage,
    /<input type="hidden" name="next" value=\{nextParam\}/,
  );
  assert.match(
    signupPage,
    /\/login\?next=\$\{encodeURIComponent\(nextParam\)\}/,
  );
  const loginPage = await source("app/(auth)/login/page.tsx");
  assert.match(
    loginPage,
    /\/signup\?next=\$\{encodeURIComponent\(nextParam\)\}/,
  );
});

test("the app shell shows Team to everyone and the picker above two workspaces", async () => {
  const shell = await source("components/app-shell.tsx");
  assert.match(shell, /href: "\/account\/team", label: "Team"/);
  assert.match(shell, /<WorkspaceSwitcher/);
  assert.match(shell, /workspaces=\{shell\.workspaces\}/);
  const switcher = await source("components/workspace-switcher.tsx");
  // The switcher hides itself with a single workspace and posts to select.
  assert.match(switcher, /workspaces\.length < 2/);
  assert.match(switcher, /action="\/app-api\/workspaces\/select"/);
  assert.match(switcher, /name="workspace_id"/);
});
