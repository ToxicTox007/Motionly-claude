// A faithful mirror of apps/api/app/services/signup_policy.py so the password
// chosen at reset time is validated with the same rules the sign-up API uses,
// without a round-trip. tests/password-policy.test.mjs asserts the common-
// password list and the constants stay in step with the Python source.

export const PASSWORD_MIN_LENGTH = 8;
// bcrypt, which Auth uses, ignores everything after the first 72 bytes.
export const PASSWORD_MAX_BYTES = 72;
export const PASSWORD_MIN_DISTINCT_CHARACTERS = 4;

export const COMMON_PASSWORDS = new Set([
  "00000000",
  "11111111",
  "12341234",
  "12344321",
  "12345678",
  "123456789",
  "1234567890",
  "123123123",
  "1q2w3e4r",
  "1q2w3e4r5t",
  "1qaz2wsx",
  "87654321",
  "88888888",
  "987654321",
  "abc12345",
  "abcd1234",
  "abcdefgh",
  "access14",
  "admin123",
  "asdfasdf",
  "asdfghjk",
  "asdfghjkl",
  "baseball",
  "basketball",
  "batman123",
  "charlie1",
  "computer",
  "corvette",
  "dragon12",
  "football",
  "football1",
  "freedom1",
  "iloveyou",
  "iloveyou1",
  "jennifer",
  "jordan23",
  "letmein1",
  "liverpool",
  "master12",
  "michelle",
  "mustang1",
  "password",
  "password1",
  "password12",
  "password123",
  "passw0rd",
  "princess",
  "qazwsxedc",
  "qwerty12",
  "qwerty123",
  "qwertyui",
  "qwertyuiop",
  "starwars",
  "sunshine",
  "superman",
  "trustno1",
  "welcome1",
  "whatever",
  "zaq12wsx",
  "motionly",
  "motionly1",
  "motionly123",
]);

const encoder = new TextEncoder();

/**
 * Why a password is refused, or null when it is acceptable. ``email`` is
 * optional: when present (as at sign-up), the local part cannot appear in the
 * password.
 */
export function passwordProblem(password, email = "") {
  if (typeof password !== "string" || password.length < PASSWORD_MIN_LENGTH) {
    return "too_short";
  }
  if (encoder.encode(password).length > PASSWORD_MAX_BYTES) {
    return "too_long";
  }
  const lowered = password.toLowerCase();
  if (COMMON_PASSWORDS.has(lowered)) {
    return "common";
  }
  if (new Set(lowered).size < PASSWORD_MIN_DISTINCT_CHARACTERS) {
    return "too_simple";
  }
  const localPart = String(email).split("@", 1)[0].toLowerCase();
  if (localPart.length >= 3 && lowered.includes(localPart)) {
    return "contains_email";
  }
  return null;
}
