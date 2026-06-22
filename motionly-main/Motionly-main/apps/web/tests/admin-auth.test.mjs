import assert from "node:assert/strict";
import test from "node:test";

import { isAdminIdentityAllowed } from "../lib/admin-auth.mjs";

const identity = {
  userId: "00000000-0000-4000-8000-000000000001",
  workspaceId: "00000000-0000-4000-8000-000000000002",
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
