// Task 26 — live collaboration, end to end against the stack.
//
// Builds directly on Task 25's private project channel. Proves the two things a
// collaborator relies on:
//   1. A change one editor saves appears to another WITHOUT a reload: member B
//      subscribes to the project's private channel `project:<id>`, member A
//      saves through the public API, and member A's save makes the API
//      broadcast `manifest_updated` on that channel — which member B receives
//      live (ids/revision only, no content).
//   2. A non-member is rejected: a user from another workspace cannot join the
//      project's private channel (RLS on realtime.messages), so collaboration
//      state never leaks across tenants.
//
// We drive supabase-js in Node with each user's real access token (the same
// token and `<origin>/supabase/realtime/v1/websocket` path a browser uses),
// because the broadcast delivery and the RLS denial are pure WebSocket
// exchanges. The save that triggers the broadcast is a real authenticated call
// to the public API, exactly as the editor's save does.
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
} from "./support/realtime.mjs";
import { addWorkspaceUser, ownedWorkspaceId } from "./support/workspace.mjs";

// A fresh confirmed owner (handle_new_user gives them their own workspace).
async function createOwner(stack) {
  const email = `collab-owner-${randomBytes(6).toString("hex")}@motionly.test`;
  const password = `Owner-${randomBytes(12).toString("hex")}-Aa1!`;
  const created = await stack.admin.post("/supabase/auth/v1/admin/users", {
    data: { email, password, email_confirm: true },
  });
  expect(created.status(), await created.text()).toBe(200);
  const body = await created.json();
  return { id: body.id, email, password };
}

/** Create a project in a workspace through the service-role REST API. */
async function createProject(stack, workspaceId, ownerId) {
  const response = await stack.admin.post("/supabase/rest/v1/projects", {
    headers: { prefer: "return=representation" },
    data: {
      workspace_id: workspaceId,
      created_by: ownerId,
      name: `Collab ${randomBytes(4).toString("hex")}`,
      status: "draft",
    },
  });
  expect(response.status(), await response.text()).toBeLessThan(300);
  const rows = await response.json();
  return String(rows[0].id);
}

/** An authenticated (user JWT) API request context, as the browser carries. */
async function memberApiContext(stack, baseURL, playwright, user, workspaceId) {
  const token = await accessTokenFor(stack, user);
  return playwright.request.newContext({
    baseURL,
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: {
      authorization: `Bearer ${token}`,
      "x-motionly-workspace-id": workspaceId,
    },
  });
}

test("a saved change reaches another editor live; non-members are rejected", async ({
  user,
  stack,
  baseURL,
  playwright,
}) => {
  const workspaceId = await ownedWorkspaceId(stack, user.id);
  const projectId = await createProject(stack, workspaceId, user.id);
  const topic = `project:${projectId}`;

  // Member B is a second active member of the SAME workspace (the "other
  // editor" who should see the change live).
  const second = await addWorkspaceUser(stack, workspaceId, "member");

  // A user who belongs to a DIFFERENT workspace, for the isolation case.
  const outsider = await createOwner(stack);

  const clients = [];
  let memberApi;
  try {
    const ownerToken = await accessTokenFor(stack, user);
    const memberToken = await accessTokenFor(stack, second.user);
    const outsiderToken = await accessTokenFor(stack, outsider);

    const memberClient = realtimeClientFor(baseURL, memberToken);
    const outsiderClient = realtimeClientFor(baseURL, outsiderToken);
    clients.push(
      realtimeClientFor(baseURL, ownerToken),
      memberClient,
      outsiderClient,
    );

    // Member B joins the project's private channel and listens for the API's
    // change broadcast.
    const received = [];
    const memberJoin = await joinPrivateTopic(memberClient, topic, {
      key: "member",
    });
    expect(
      memberJoin.status,
      `member joins ${topic}: ${memberJoin.error ?? ""}`,
    ).toBe("joined");
    memberJoin.channel.on(
      "broadcast",
      { event: "manifest_updated" },
      (message) => received.push(message),
    );

    // Member A saves through the public API — the same authenticated path the
    // editor uses — which makes the API broadcast on the private channel.
    memberApi = await memberApiContext(
      stack,
      baseURL,
      playwright,
      user,
      workspaceId,
    );
    const saved = await memberApi.patch(
      `/api/projects/${projectId}/phase5/collaboration`,
      { data: { base_revision: 0, manifest: { title: "Shared cut" } } },
    );
    expect(saved.status(), await saved.text()).toBe(200);
    const savedBody = await saved.json();

    // Member B receives the change live, carrying only ids/revision.
    await expect
      .poll(() => received.length, { timeout: 15_000 })
      .toBeGreaterThan(0);
    const payload = received[0].payload;
    expect(payload.project_id).toBe(projectId);
    expect(Number(payload.revision)).toBe(savedBody.revision);
    // No plan/manifest content ever travels over the channel.
    expect(payload.manifest).toBeUndefined();
    expect(payload.title).toBeUndefined();

    // A member of another workspace cannot join the project's private channel.
    const outsiderJoin = await joinPrivateTopic(outsiderClient, topic, {
      key: "outsider",
      timeoutMs: 12_000,
    });
    expect(
      outsiderJoin.status,
      "a non-member cannot join the project channel",
    ).toBe("denied");
  } finally {
    if (memberApi) await memberApi.dispose();
    for (const client of clients) {
      await closeRealtime(client);
    }
    await second.cleanup();
    await stack.admin.delete(`/supabase/auth/v1/admin/users/${outsider.id}`);
  }
});
