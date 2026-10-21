// Sign-up page errors, from the API's POST /auth/signup refusals
// (apps/api/app/api/auth.py). The page shows them via ?error=<reason>.

export const SIGNUP_ERROR_MESSAGES = {
  email_taken: "An account with this email already exists. Log in instead.",
  password_weak:
    "Choose a stronger password: at least 8 characters, not a common password, and not your email address.",
  email_invalid: "Enter a valid email address.",
  name_required: "Enter your name (up to 120 characters).",
  rate_limited:
    "Too many sign-ups from your network. Wait an hour, then try again.",
  system_error:
    "We could not create your account right now. Try again in a few minutes.",
};

const BY_CODE = {
  email_taken: "email_taken",
  password_weak: "password_weak",
  email_invalid: "email_invalid",
  name_required: "name_required",
  signup_rate_limited: "rate_limited",
};

/** The sign-up page's error reason for an API refusal. */
export function signupErrorReason(status, code) {
  if (status === 429) return "rate_limited";
  return code && Object.hasOwn(BY_CODE, code) ? BY_CODE[code] : "system_error";
}
