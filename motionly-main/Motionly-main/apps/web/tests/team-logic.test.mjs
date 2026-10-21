// Pure team/seat logic (Task 21). Seat accounting, the invite-token fragment
// reader, and the owner-vs-admin upgrade prompt all have to behave the same on
// the server (the Team page) and in the browser (the /invite page), so they
// live in a framework-free module that is unit-tested directly here.
import assert from "node:assert/strict";
import test from "node:test";

import {
  INVITE_TOKEN_STORAGE_KEY,
  canInvite,
  inviteTokenFromHash,
  invitationIsPending,
  seatUsage,
  upgradePrompt,
} from "../lib/team.mjs";

test("the invite token is read from the URL fragment, never a query string", () => {
  assert.equal(inviteTokenFromHash("#token=abc123"), "abc123");
  assert.equal(inviteTokenFromHash("token=abc123"), "abc123");
  // Extra fragment params don't confuse it.
  assert.equal(inviteTokenFromHash("#token=abc123&x=1"), "abc123");
  // Absent / empty.
  assert.equal(inviteTokenFromHash(""), null);
  assert.equal(inviteTokenFromHash("#"), null);
  assert.equal(inviteTokenFromHash("#other=1"), null);
  assert.equal(inviteTokenFromHash("#token="), null);
  assert.equal(inviteTokenFromHash("#token=%20%20"), null);
  assert.equal(inviteTokenFromHash(undefined), null);
  assert.equal(inviteTokenFromHash(null), null);
});

test("the storage key is stable so the token survives a login/signup detour", () => {
  assert.equal(INVITE_TOKEN_STORAGE_KEY, "motionly_invite_token");
});

test("seat usage counts active members plus live invitations against the plan", () => {
  const usage = seatUsage({ activeMembers: 2, pendingInvites: 1, limit: 5 });
  assert.equal(usage.used, 3);
  assert.equal(usage.limit, 5);
  assert.equal(usage.remaining, 2);
  assert.equal(usage.atLimit, false);
  assert.equal(usage.label, "3 / 5");
});

test("seat usage clamps at the limit and never reports negative remaining", () => {
  const full = seatUsage({ activeMembers: 5, pendingInvites: 1, limit: 5 });
  assert.equal(full.used, 6);
  assert.equal(full.remaining, 0);
  assert.equal(full.atLimit, true);
  // Defaults: a free plan is a single seat already used by the owner.
  const free = seatUsage({ activeMembers: 1 });
  assert.equal(free.limit, 1);
  assert.equal(free.atLimit, true);
});

test("inviting requires owner/admin AND a free seat", () => {
  const free = seatUsage({ activeMembers: 1, limit: 5 });
  const full = seatUsage({ activeMembers: 5, limit: 5 });
  assert.equal(canInvite("owner", free), true);
  assert.equal(canInvite("admin", free), true);
  assert.equal(canInvite("member", free), false);
  assert.equal(canInvite("owner", full), false);
  assert.equal(canInvite("owner", null), false);
});

test("the upgrade prompt links owners to billing and tells admins the owner must act", () => {
  const owner = upgradePrompt("owner");
  assert.equal(owner.canUpgrade, true);
  assert.equal(owner.actionHref, "/billing");
  assert.match(owner.message, /Upgrade/);

  const admin = upgradePrompt("admin");
  assert.equal(admin.canUpgrade, false);
  assert.equal(admin.actionHref, null);
  assert.match(admin.message, /owner to upgrade/i);
});

test("only live invitations render as pending", () => {
  const future = new Date(Date.now() + 86_400_000).toISOString();
  const past = new Date(Date.now() - 1000).toISOString();
  assert.equal(invitationIsPending({ expires_at: future }), true);
  assert.equal(invitationIsPending({ expires_at: past }), false);
  assert.equal(invitationIsPending({ revoked_at: future }), false);
  assert.equal(invitationIsPending({ accepted_at: past }), false);
  assert.equal(invitationIsPending(null), false);
});
