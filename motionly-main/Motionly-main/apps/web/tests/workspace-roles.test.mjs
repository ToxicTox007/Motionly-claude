// Role-based hiding in the web app (Task 19): only the owner manages billing;
// owner/admin manage the workspace (brand kits, templates, team, API keys);
// members can edit and render. These pure predicates drive the app-shell nav
// and the billing page's "only the owner can manage billing" state.
import assert from "node:assert/strict";
import test from "node:test";

import {
  canManageBilling,
  canManageWorkspace,
  normalizeWorkspaceRole,
} from "../lib/workspace-roles.mjs";

test("only the owner can manage billing", () => {
  assert.equal(canManageBilling("owner"), true);
  assert.equal(canManageBilling("admin"), false);
  assert.equal(canManageBilling("member"), false);
});

test("owner and admin can manage the workspace; members cannot", () => {
  assert.equal(canManageWorkspace("owner"), true);
  assert.equal(canManageWorkspace("admin"), true);
  assert.equal(canManageWorkspace("member"), false);
});

test("an unknown or missing role is treated as the least-privileged member", () => {
  for (const value of [undefined, null, "", "superuser", 42]) {
    assert.equal(normalizeWorkspaceRole(value), "member");
    assert.equal(canManageBilling(value), false);
    assert.equal(canManageWorkspace(value), false);
  }
});

test("members see neither billing nor workspace management", () => {
  // Mirrors the app-shell nav filter and billing page owner-only banner.
  const role = "member";
  const showBilling = canManageBilling(role);
  const showWorkspaceManagement = canManageWorkspace(role);
  assert.equal(showBilling, false);
  assert.equal(showWorkspaceManagement, false);
});
