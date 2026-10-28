import {
  PASSWORD_MIN_LENGTH,
  passwordProblem,
  type PasswordProblem,
} from "@/lib/password-policy.mjs";

// What the sign-up form tells the viewer about a password, derived from the
// same policy the API enforces (lib/password-policy.mjs mirrors it), so the
// meter never says "Very strong" for a password the server will refuse.

const PROBLEM_MESSAGES: Record<PasswordProblem, string> = {
  too_short: `Use at least ${PASSWORD_MIN_LENGTH} characters.`,
  too_long: "Use a shorter password (at most 72 bytes).",
  common: "That password is too common. Choose something less guessable.",
  too_simple: "Use more different characters.",
  contains_email: "Don't include the name part of your email.",
};

export type PasswordFeedback = {
  /** 1 (refused) … 4 (very strong). */
  score: 1 | 2 | 3 | 4;
  label: string;
  /** Why the policy refuses it, or null when it is acceptable. */
  problem: string | null;
};

export function passwordFeedback(
  password: string,
  email = "",
): PasswordFeedback {
  const problem = passwordProblem(password, email);
  if (problem) {
    return { score: 1, label: "Too weak", problem: PROBLEM_MESSAGES[problem] };
  }
  const variety = new Set(password.toLowerCase()).size;
  if (password.length >= 14 && variety >= 8) {
    return { score: 4, label: "Very strong", problem: null };
  }
  if (password.length >= 11) {
    return { score: 3, label: "Strong", problem: null };
  }
  return { score: 2, label: "Acceptable", problem: null };
}

export const PASSWORD_HINT = `At least ${PASSWORD_MIN_LENGTH} characters with some variety; not a common password or your email name.`;
