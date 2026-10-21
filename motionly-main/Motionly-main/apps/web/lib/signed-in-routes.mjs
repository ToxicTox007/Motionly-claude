// Pages that need a signed-in viewer: one prefix per section of app/(app)
// (tests/sessions-and-redirects.test.mjs keeps the two in step). proxy.ts
// sends signed-out viewers to the login page and renews the session here.
//
// /reset-password is the exception: it lives in app/(auth) but needs the
// recovery session that /auth/confirm just set, so it is guarded too. Without
// a session the proxy bounces the visitor to log in rather than show a form
// that cannot work.
export const SIGNED_IN_PREFIXES = Object.freeze([
  "/account",
  "/admin",
  "/billing",
  "/dashboard",
  "/projects",
  "/reset-password",
]);

export function isSignedInPath(pathname) {
  return SIGNED_IN_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
