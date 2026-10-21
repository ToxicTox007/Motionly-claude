// Shared browser-suite helpers for workspace membership and billing state.
//
// Every test creates the state it needs deterministically through the running
// stack — never by guarding an assertion behind `if (visible)`. Membership is
// written with the service-role `stack.admin` context (Auth admin API +
// PostgREST on workspace_members); billing state is written through PostgREST
// on the `subscriptions` table (the same path the API's webhook handler uses).
import { randomBytes } from "node:crypto";

import { expect } from "@playwright/test";

/** The id of the workspace the given user owns (handle_new_user creates one). */
export async function ownedWorkspaceId(stack, userId) {
  const response = await stack.admin.get(
    `/supabase/rest/v1/workspace_members?user_id=eq.${userId}&role=eq.owner&status=eq.active&select=workspace_id`,
  );
  expect(response.status(), await response.text()).toBe(200);
  const rows = await response.json();
  expect(rows.length, "owner should have exactly one workspace").toBe(1);
  return String(rows[0].workspace_id);
}

/**
 * Create a confirmed user and add them to `workspaceId` with the given role
 * ("member", "admin", …). Returns the new user's credentials and a cleanup
 * function that deletes the account (which cascades its membership).
 */
export async function addWorkspaceUser(stack, workspaceId, role = "member") {
  const email = `${role}-${randomBytes(6).toString("hex")}@motionly.test`;
  const password = `Member-${randomBytes(12).toString("hex")}-Aa1!`;
  const created = await stack.admin.post("/supabase/auth/v1/admin/users", {
    data: { email, password, email_confirm: true },
  });
  expect(created.status(), await created.text()).toBe(200);
  const member = await created.json();

  const membership = await stack.admin.post(
    "/supabase/rest/v1/workspace_members",
    {
      headers: { prefer: "return=minimal" },
      data: {
        workspace_id: workspaceId,
        user_id: member.id,
        role,
        status: "active",
      },
    },
  );
  expect(membership.status(), await membership.text()).toBeLessThan(300);

  return {
    user: { id: member.id, email, password },
    cleanup: async () => {
      await stack.admin.delete(`/supabase/auth/v1/admin/users/${member.id}`);
    },
  };
}

/**
 * Write a subscription row for a workspace through PostgREST, the authoritative
 * store the API reads for /billing/status. Defaults to an active paid plan;
 * callers override `status` / `cancel_at_period_end` to reach cancel-scheduled
 * or past-due states deterministically (no reliance on the renewal timer).
 */
export async function setSubscription(
  stack,
  workspaceId,
  {
    plan = "agency",
    status = "active",
    cancelAtPeriodEnd = false,
    gatewaySubscriptionId = null,
  } = {},
) {
  const now = new Date();
  const periodEnd = new Date(now.getTime() + 30 * 24 * 3600 * 1000);
  const response = await stack.admin.post("/supabase/rest/v1/subscriptions", {
    headers: {
      prefer: "resolution=merge-duplicates,return=representation",
    },
    params: { on_conflict: "workspace_id" },
    data: {
      workspace_id: workspaceId,
      plan,
      status,
      cancel_at_period_end: cancelAtPeriodEnd,
      current_period_start: now.toISOString(),
      current_period_end: periodEnd.toISOString(),
      gateway_subscription_id: gatewaySubscriptionId,
      gateway_price_id: plan,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

/** Grant a workspace a paid plan with seats/features (Agency). */
export async function grantAgencyPlan(stack, workspaceId) {
  return setSubscription(stack, workspaceId, {
    plan: "agency",
    status: "active",
  });
}

/**
 * Seed a captured payment (a "charge" billing adjustment) for a workspace
 * through PostgREST — the same store the API's /billing/history endpoint reads.
 * Lets a test assert the payment appears in history and is later reversed by a
 * refund, deterministically, without waiting on the gateway webhook. Amount is
 * in INR paise. Returns the created row.
 */
export async function seedPaymentCharge(
  stack,
  workspaceId,
  { plan = "agency", amountPaise = 49900, gatewayEventId = null } = {},
) {
  const eventId = gatewayEventId ?? `charge_${randomBytes(8).toString("hex")}`;
  const response = await stack.admin.post(
    "/supabase/rest/v1/billing_adjustments",
    {
      headers: { prefer: "return=representation" },
      data: {
        workspace_id: workspaceId,
        gateway_event_id: eventId,
        adjustment_type: "charge",
        amount_paise: amountPaise,
        currency: "inr",
        metadata: { plan },
      },
    },
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  const [row] = await response.json();
  return row;
}

/** Every billing adjustment (charges + refunds) for a workspace, newest first. */
export async function listBillingAdjustments(stack, workspaceId) {
  const response = await stack.admin.get(
    `/supabase/rest/v1/billing_adjustments?workspace_id=eq.${workspaceId}&order=created_at.desc&select=adjustment_type,amount_paise,currency`,
  );
  expect(response.status(), await response.text()).toBe(200);
  return response.json();
}
