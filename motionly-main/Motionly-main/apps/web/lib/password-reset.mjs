// Copy for the forgot-password and reset-password flows (Task 12). The reset
// never reveals whether an email has an account: the request page always shows
// the same confirmation.

// Shown after POST /auth/password-reset-requests, whatever the email was.
export const RESET_REQUEST_CONFIRMATION =
  "If an account exists for that email, an admin will set up a way for you to get back in. Check with your workspace admin.";

export const RESET_REQUEST_ERROR_MESSAGES = {
  rate_limited:
    "Too many requests from your network. Wait an hour, then try again.",
  system_error:
    "We could not record that request right now. Try again in a few minutes.",
};

/** The forgot-password page's error reason for an API refusal. */
export function resetRequestErrorReason(status) {
  if (status === 429) return "rate_limited";
  return "system_error";
}

// Shown on the /auth/confirm error page when a recovery link cannot be used.
export const RESET_LINK_ERROR_MESSAGES = {
  expired:
    "This reset link has expired or has already been used. Ask your admin for a new one.",
  invalid: "This reset link is not valid. Ask your admin for a new one.",
  missing: "This reset link is incomplete. Ask your admin for a new one.",
};

export function resetLinkErrorReason(code) {
  if (code === "otp_expired") return "expired";
  if (code === "missing_token") return "missing";
  return "invalid";
}

// Shown on /reset-password after the new password is rejected.
export const RESET_PASSWORD_ERROR_MESSAGES = {
  password_weak:
    "Choose a stronger password: at least 8 characters, not a common password.",
  no_session: "Your reset link has expired. Ask your admin for a new one.",
  same_password: "Choose a password you have not used before.",
  system_error:
    "We could not set your new password right now. Try again in a few minutes.",
};
