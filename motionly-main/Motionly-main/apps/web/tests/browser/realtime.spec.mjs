// Task 25 — Supabase Realtime private channels, end to end against the stack.
//
// Proves the tenant isolation a workspace relies on for its live channel:
//   1. Two active members of the SAME workspace each join the private topic
//      `workspace:<id>` through the CDN proxy and see each other in presence.
//   2. A member of ANOTHER workspace is denied the join (RLS on
//      realtime.messages rejects the private topic).
//   3. A signed-out visitor (no JWT, so no auth.uid()) is denied.
//
// Authorization is RLS: realtime.motionly_can_access_topic(realtime.topic())
// must pass for both the SELECT (join) and INSERT (presence) policies. We drive
// the real supabase-js client in Node with each user's access token — the same
// token and the same `<origin>/supabase/realtime/v1/websocket` path a browser
// would use — because presence is a pure WebSocket exchange with no UI.
//
// Written for the phase checkpoint; NOT run in this task (CONTEXT: the full CPU
// stack is not started here, browser specs are --list-checked only).
import { randomBytes } from "node:crypto";

import { expect, test } from "./support/fixtures.mjs";
import {
  accessTokenFor,
  closeRealtime,
  joinPrivateTopic,
  realtimeClientFor,
  waitForPresence,
} from "./support/realtime.mjs";
import { addWorkspaceUser, ownedWorkspaceId } from "./support/workspace.mjs";

test("members of a workspace share its private channel; outsiders are denied", async ({
  user,
  stack,
  baseURL,
}) => {
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  const topic = `workspace:${workspaceId}`;

  // A second active member of the SAME workspace.
  const second = await addWorkspaceUser(stack, workspaceId, "member");

  // The isolation case needs a user who belongs to a DIFFERENT workspace; the
  // simplest independent tenant is a brand-new owner with their own workspace.
  const other = await createOwner(stack);
  const otherWorkspaceId = await ownedWorkspaceId(stack, other.id);
  expect(otherWorkspaceId).not.toBe(workspaceId);

  const clients = [];
  try {
    const ownerToken = await accessTokenFor(stack, user);
    const memberToken = await accessTokenFor(stack, second.user);
    const otherToken = await accessTokenFor(stack, other);

    const ownerClient = realtimeClientFor(baseURL, ownerToken);
    const memberClient = realtimeClientFor(baseURL, memberToken);
    const otherClient = realtimeClientFor(baseURL, otherToken);
    const guestClient = realtimeClientFor(baseURL, null);
    clients.push(ownerClient, memberClient, otherClient, guestClient);

    // 1. Both members of the workspace join its private topic.
    const ownerJoin = await joinPrivateTopic(ownerClient, topic, {
      key: "owner",
      meta: { user: "owner" },
    });
    expect(
      ownerJoin.status,
      `the workspace owner joins ${topic}: ${ownerJoin.error ?? ""}`,
    ).toBe("joined");

    const memberJoin = await joinPrivateTopic(memberClient, topic, {
      key: "member",
      meta: { user: "member" },
    });
    expect(
      memberJoin.status,
      `a second member joins ${topic}: ${memberJoin.error ?? ""}`,
    ).toBe("joined");

    // They see EACH OTHER in presence.
    await waitForPresence(ownerJoin.channel, "member");
    await waitForPresence(memberJoin.channel, "owner");
    expect(
      Object.keys(ownerJoin.presenceState()).sort(),
      "the owner sees both members present",
    ).toEqual(["member", "owner"]);
    expect(
      Object.keys(memberJoin.presenceState()).sort(),
      "the member sees both members present",
    ).toEqual(["member", "owner"]);

    // 2. A member of another workspace is denied this topic.
    const outsiderJoin = await joinPrivateTopic(otherClient, topic, {
      key: "outsider",
      meta: { user: "outsider" },
      timeoutMs: 12_000,
    });
    expect(
      outsiderJoin.status,
      "a member of another workspace cannot join the topic",
    ).toBe("denied");

    // 3. A signed-out visitor (no JWT) is denied.
    const guestJoin = await joinPrivateTopic(guestClient, topic, {
      key: "guest",
      meta: { user: "guest" },
      timeoutMs: 12_000,
    });
    expect(
      guestJoin.status,
      "a signed-out visitor cannot join the private topic",
    ).toBe("denied");

    // The denied joiners never appear in the members' presence.
    const ownerPresence = Object.keys(ownerJoin.presenceState());
    expect(ownerPresence).not.toContain("outsider");
    expect(ownerPresence).not.toContain("guest");
  } finally {
    for (const client of clients) {
      await closeRealtime(client);
    }
    await second.cleanup();
    await stack.admin.delete(`/supabase/auth/v1/admin/users/${other.id}`);
  }
});

// A fresh confirmed owner (handle_new_user gives them their own workspace).
async function createOwner(stack) {
  const email = `realtime-owner-${randomBytes(6).toString("hex")}@motionly.test`;
  const password = `Owner-${randomBytes(12).toString("hex")}-Aa1!`;
  const created = await stack.admin.post("/supabase/auth/v1/admin/users", {
    data: { email, password, email_confirm: true },
  });
  expect(created.status(), await created.text()).toBe(200);
  const body = await created.json();
  return { id: body.id, email, password };
}
