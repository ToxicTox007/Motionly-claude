// User-facing copy for error and notice codes that travel in URLs (?error=,
// ?notice=, ?done=) or come back from the API. Codes are looked up in fixed
// allowlists; an unknown code is never echoed back to the page, so a crafted
// link cannot put attacker-written text inside trusted UI.

export type MessageDomain =
  | "account"
  | "admin"
  | "billing"
  | "dashboard"
  | "editorial"
  | "feedback"
  | "projects"
  | "team";

const SHARED_ERRORS: Record<string, string> = {
  network_error:
    "Motionly could not reach its servers. Check your connection and try again.",
  timeout: "The request took too long. Please try again.",
  rate_limit_exceeded: "Too many requests. Wait a moment and try again.",
  workspace_role_forbidden: "Your role in this workspace does not allow that.",
  auth_required: "Your session ended. Sign in again to continue.",
};

const ERRORS: Record<MessageDomain, Record<string, string>> = {
  account: {
    profile_update_failed: "Your profile could not be saved. Try again.",
    password_mismatch: "The new passwords do not match.",
    password_weak:
      "Choose a stronger password: at least 8 characters with some variety, and not a common password.",
    current_password_wrong: "Your current password is not correct.",
    current_password_required: "Enter your current password.",
    same_password: "Choose a password you have not used for this account.",
    rate_limited: "Too many attempts. Wait a few minutes and try again.",
    system_error: "Something went wrong. Please try again.",
  },
  admin: {
    admin_access_denied: "You do not have access to the admin area.",
    admin_required: "You do not have access to the admin area.",
    user_not_found: "No account uses that email.",
    reset_link_failed: "Could not create a reset link. Try again.",
    email_required: "Enter an email to search.",
    request_required: "Select a request to act on.",
    user_required: "Select a user to act on.",
    job_required: "Select a job to act on.",
    ban_failed: "The account could not be banned. Try again.",
    unban_failed: "The account could not be unbanned. Try again.",
    dismiss_failed: "The request could not be dismissed. Try again.",
    retry_failed: "The job could not be retried. Try again.",
    cancel_failed: "The job could not be cancelled. Try again.",
    dead_letter_failed: "Stuck jobs could not be dead-lettered. Try again.",
    action_failed: "That action could not be completed. Try again.",
  },
  billing: {
    checkout_failed: "Checkout could not be started. Try again.",
    checkout_unavailable: "Checkout is unavailable right now. Try again later.",
    cancel_failed: "The subscription could not be cancelled. Try again.",
    retry_failed: "The payment could not be retried. Try again.",
    billing_owner_only: "Only the workspace owner can manage billing.",
    billing_disabled: "Billing is not enabled for this deployment.",
    plan_invalid: "Choose one of the listed plans.",
  },
  dashboard: {
    workspace_missing: "You are not a member of any workspace.",
    workspace_invalid: "That workspace is not available to you.",
    workspace_selection_required: "Choose a workspace to continue.",
    workspace_unavailable: "That workspace is not available right now.",
    admin_access_denied: "You do not have access to the admin area.",
    admin_required: "You do not have access to the admin area.",
  },
  editorial: {
    promote_confirmation_required: "Type PROMOTE to confirm the promotion.",
    action_failed: "That action could not be completed. Try again.",
  },
  feedback: {
    message_required: "Write at least 10 characters of feedback.",
    message_too_long: "Keep feedback under 2,000 characters.",
    feedback_failed: "Feedback could not be sent. Try again.",
  },
  projects: {
    missing_project: "Choose a project first.",
    not_authenticated: "Your session ended. Sign in again to continue.",
    title_required: "Give the project a title.",
    status_invalid: "Choose Draft, Ready or Failed.",
    project_not_found: "That project no longer exists.",
    update_failed: "The project could not be updated. Try again.",
    delete_failed: "The project could not be deleted. Try again.",
    create_failed: "The project could not be created. Try again.",
  },
  team: {
    invite_failed: "The invite could not be created. Try again.",
    seat_limit_reached: "Every seat on this plan is in use.",
  },
};

const NOTICES: Partial<Record<MessageDomain, Record<string, string>>> = {
  admin: {
    banned: "The account is banned.",
    unbanned: "The account is unbanned.",
    dismissed: "The reset request is dismissed.",
    retried: "The job is queued again.",
    cancelled: "The job is cancelled.",
    dead_lettered: "Stuck jobs were moved to the dead-letter state.",
  },
  billing: {
    cancel_scheduled:
      "Your subscription is cancelled. It stays active until the end of the current period.",
    payment_retried: "The payment is being retried.",
  },
  dashboard: {
    password_updated: "Your password is updated.",
  },
  projects: {
    updated: "Project updated.",
    deleted: "Project deleted.",
  },
};

const GENERIC_ERROR = "That action could not be completed. Please try again.";

/** Copy for an error code; unknown or missing codes get a generic message. */
export function messageFor(
  domain: MessageDomain,
  code: string | null | undefined,
): string {
  if (!code) return GENERIC_ERROR;
  return ERRORS[domain][code] ?? SHARED_ERRORS[code] ?? GENERIC_ERROR;
}

/** Copy for a known error code, or null when the code is not recognised. */
export function knownErrorFor(
  domain: MessageDomain,
  code: string | null | undefined,
): string | null {
  if (!code) return null;
  return ERRORS[domain][code] ?? SHARED_ERRORS[code] ?? null;
}

/** Copy for a success notice code; unknown codes show nothing. */
export function noticeFor(
  domain: MessageDomain,
  code: string | null | undefined,
): string | null {
  if (!code) return null;
  return NOTICES[domain]?.[code] ?? null;
}

/** The first string value of a search param, or null. */
export function searchParam(
  params: Record<string, string | string[] | undefined>,
  name: string,
): string | null {
  const value = params[name];
  if (typeof value === "string") return value;
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  return null;
}
