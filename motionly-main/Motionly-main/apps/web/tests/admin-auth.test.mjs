import assert from "node:assert/strict";
import test from "node:test";

import { isAdminIdentityAllowed } from "../lib/admin-auth.mjs";

const identity = {
  userId: "00000000-0000-4000-8000-000000000001",
  workspaceId: "00000000-0000-4000-8000-000000000002",
  role: "owner",
};

test("rejects every authenticated identity when no admin allowlist is configured", () => {
  assert.equal(
    isAdminIdentityAllowed(identity, {
      adminUserIds: "",
      adminWorkspaceIds: "",
    }),
    false,
  );
});

test("rejects an authenticated non-admin even when another identity is allowed", () => {
  assert.equal(
    isAdminIdentityAllowed(identity, {
      adminUserIds: "00000000-0000-4000-8000-000000000099",
      adminWorkspaceIds: "00000000-0000-4000-8000-000000000098",
    }),
    false,
  );
});

test("accepts an explicitly allowed user", () => {
  assert.equal(
    isAdminIdentityAllowed(identity, {
      adminUserIds: `  ${identity.userId.toUpperCase()}  `,
      adminWorkspaceIds: "",
    }),
    true,
  );
});

test("accepts an explicitly allowed workspace", () => {
  assert.equal(
    isAdminIdentityAllowed(identity, {
      adminUserIds: "",
      adminWorkspaceIds: identity.workspaceId,
    }),
    true,
  );
});

test("an allowed workspace admits its owner and admins only, never plain members", () => {
  for (const [role, expected] of [
    ["owner", true],
    ["admin", true],
    ["ADMIN", true],
    ["member", false],
    ["", false],
    [undefined, false],
  ]) {
    assert.equal(
      isAdminIdentityAllowed(
        { ...identity, role },
        { adminUserIds: "", adminWorkspaceIds: identity.workspaceId },
      ),
      expected,
      `role ${String(role)}`,
    );
  }
});

test("an explicitly allowed user is an admin whatever their workspace role", () => {
  assert.equal(
    isAdminIdentityAllowed(
      { ...identity, role: "member" },
      { adminUserIds: identity.userId, adminWorkspaceIds: "" },
    ),
    true,
  );
});
