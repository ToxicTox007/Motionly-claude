// Pure team/seat logic (Task 21), shared by the Team settings page and the
// public /invite page. Kept framework-free so it can be unit-tested directly
// and imported by both server components and client components.

/** The sessionStorage key that carries an invite token across a login/signup
 * detour, so a signed-out visitor who follows an invite link keeps the token
 * when they come back to /invite. */
export const INVITE_TOKEN_STORAGE_KEY = "motionly_invite_token";

/**
 * Read the invite token from a URL fragment. The token travels ONLY in the
 * fragment (`/invite#token=...`) so it is never sent to a server in a request
 * line, logged, or captured by the Referer header. Returns null when absent.
 */
export function inviteTokenFromHash(hash) {
  if (typeof hash !== "string" || hash.length === 0) return null;
  const fragment = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!fragment) return null;
  const params = new URLSearchParams(fragment);
  const token = params.get("token");
  if (!token) return null;
  const trimmed = token.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Seat usage for a workspace. A seat is consumed by every active member and by
 * every live (pending, unexpired, unrevoked) invitation, so an owner can't mint
 * more invites than the plan allows. `limit` is the plan's `seats` entitlement.
 */
export function seatUsage({
  activeMembers = 0,
  pendingInvites = 0,
  limit = 1,
}) {
  const used = Math.max(0, activeMembers) + Math.max(0, pendingInvites);
  const safeLimit = Math.max(0, limit);
  const remaining = Math.max(0, safeLimit - used);
  return {
    used,
    limit: safeLimit,
    remaining,
    atLimit: used >= safeLimit,
    label: `${used} / ${safeLimit}`,
  };
}

/** Whether the "invite a teammate" control should be enabled: only when the
 * caller can manage the workspace AND a seat is free. */
export function canInvite(role, usage) {
  return (
    (role === "owner" || role === "admin") && Boolean(usage) && !usage.atLimit
  );
}

/**
 * What to tell the user when seats run out. Owners can fix it themselves
 * (link to billing); admins cannot upgrade a plan, so they're told the owner
 * must. Members never see this (they get the read-only list).
 */
export function upgradePrompt(role) {
  if (role === "owner") {
    return {
      canUpgrade: true,
      message:
        "You've used every seat on your plan. Upgrade to invite more teammates.",
      actionLabel: "View plans",
      actionHref: "/billing",
    };
  }
  return {
    canUpgrade: false,
    message:
      "Every seat on this plan is in use. Ask the workspace owner to upgrade the plan to add more teammates.",
    actionLabel: null,
    actionHref: null,
  };
}

/** True once an invitation is no longer live (accepted or revoked). The server
 * is the source of truth; this only decides how a row renders. */
export function invitationIsPending(invitation) {
  if (!invitation) return false;
  if (invitation.revoked_at) return false;
  if (invitation.accepted_at || invitation.accepted_by) return false;
  if (invitation.expires_at) {
    const expires = Date.parse(invitation.expires_at);
    if (Number.isFinite(expires) && expires <= Date.now()) return false;
  }
  return true;
}
