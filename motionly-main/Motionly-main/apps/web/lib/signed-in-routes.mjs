// Pages that need a signed-in viewer: one prefix per section of app/(app)
// (tests/sessions-and-redirects.test.mjs keeps the two in step). proxy.ts
// sends signed-out viewers to the login page and renews the session here.
export const SIGNED_IN_PREFIXES = Object.freeze([
  "/account",
  "/admin",
  "/billing",
  "/dashboard",
  "/projects",
]);

export function isSignedInPath(pathname) {
  return SIGNED_IN_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
