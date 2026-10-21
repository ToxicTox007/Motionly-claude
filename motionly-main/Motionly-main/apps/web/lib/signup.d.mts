export type SignupErrorReason =
  | "email_taken"
  | "password_weak"
  | "email_invalid"
  | "name_required"
  | "rate_limited"
  | "system_error";

export const SIGNUP_ERROR_MESSAGES: Record<SignupErrorReason, string>;

export function signupErrorReason(
  status: number,
  code: string | undefined,
): SignupErrorReason;
