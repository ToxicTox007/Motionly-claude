// Renews the session in proxy.ts, the only server code that can save the
// rotated refresh token for every page. auth-js renews a token in its last
// 90 s; the proxy renews earlier, so the server components of the same
// request (which cannot write cookies) always see a token they need not renew.
export const SESSION_RENEWAL_MARGIN_MS = 120_000;

export async function renewSession(auth, now = Date.now()) {
  const {
    data: { session },
  } = await auth.getSession();
  if (!session) return { user: null };
  if (
    session.expires_at &&
    session.expires_at * 1000 - now < SESSION_RENEWAL_MARGIN_MS
  ) {
    // A failed renewal leaves the old token; getUser then decides.
    await auth.refreshSession();
  }
  const {
    data: { user },
  } = await auth.getUser();
  return { user: user ?? null };
}
