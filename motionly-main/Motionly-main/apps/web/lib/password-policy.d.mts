export const PASSWORD_MIN_LENGTH: number;
export const PASSWORD_MAX_BYTES: number;
export const PASSWORD_MIN_DISTINCT_CHARACTERS: number;
export const COMMON_PASSWORDS: Set<string>;

export type PasswordProblem =
  | "too_short"
  | "too_long"
  | "common"
  | "too_simple"
  | "contains_email";

export function passwordProblem(
  password: string,
  email?: string,
): PasswordProblem | null;
