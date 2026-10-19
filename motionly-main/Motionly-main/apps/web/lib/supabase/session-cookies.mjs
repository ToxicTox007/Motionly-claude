// Writes refreshed Supabase session cookies from the server client. Next.js
// allows cookie writes only in server actions and route handlers; in a server
// component the write throws. proxy.ts refreshes the session and writes the
// cookies for every page request, so a server component can skip it.
const READ_ONLY_COOKIES =
  /Cookies can only be modified in a Server Action or Route Handler/;

export function writeSessionCookies(store, cookiesToSet) {
  try {
    for (const { name, value, options } of cookiesToSet)
      store.set(name, value, options);
  } catch (error) {
    if (!READ_ONLY_COOKIES.test(String(error?.message ?? error))) throw error;
  }
}
