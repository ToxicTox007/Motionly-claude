export const RESET_REQUEST_CONFIRMATION: string;

export type ResetRequestErrorReason = "rate_limited" | "system_error";
export const RESET_REQUEST_ERROR_MESSAGES: Record<
  ResetRequestErrorReason,
  string
>;
export function resetRequestErrorReason(
  status: number,
): ResetRequestErrorReason;

export type ResetLinkErrorReason = "expired" | "invalid" | "missing";
export const RESET_LINK_ERROR_MESSAGES: Record<ResetLinkErrorReason, string>;
export function resetLinkErrorReason(
  code: string | undefined,
): ResetLinkErrorReason;

export type ResetPasswordErrorReason =
  | "password_weak"
  | "no_session"
  | "same_password"
  | "system_error";
export const RESET_PASSWORD_ERROR_MESSAGES: Record<
  ResetPasswordErrorReason,
  string
>;
