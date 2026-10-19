import { createServerClient, type SetAllCookies } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { getServerConfig } from "@/lib/env";
import { authCookieOptions } from "@/lib/runtime-config.mjs";
import {
  buildContentSecurityPolicy,
  createNonce,
} from "@/lib/security-headers.mjs";
import { isSignedInPath } from "@/lib/signed-in-routes.mjs";
import { renewSession } from "@/lib/supabase/session-renewal.mjs";
import { viewerAddressHeaders } from "@/lib/viewer-address.mjs";

export async function proxy(request: NextRequest) {
  // Invalid configuration throws here: requests fail closed, never unchecked.
  const config = getServerConfig();
  const nonce = createNonce();
  const contentSecurityPolicy = buildContentSecurityPolicy({
    nonce,
    publicOrigin: config.publicOrigin,
    development: process.env.NODE_ENV !== "production",
  });

  // Next.js reads the nonce from the request's CSP header for its own
  // scripts; the root layout reads x-nonce for ours.
  const next = () => {
    const headers = new Headers(request.headers);
    headers.set("x-nonce", nonce);
    headers.set("content-security-policy", contentSecurityPolicy);
    const response = NextResponse.next({ request: { headers } });
    response.headers.set("content-security-policy", contentSecurityPolicy);
    return response;
  };

  let response = next();
  if (!isSignedInPath(request.nextUrl.pathname) || config.skipAuthMiddleware) {
    return response;
  }

  const supabase = createServerClient(
    config.supabaseUrl,
    config.supabaseAnonKey,
    {
      cookieOptions: authCookieOptions(config.publicOrigin),
      // Auth rate-limits per viewer, not per web server.
      global: { headers: viewerAddressHeaders(request.headers) },
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet: Parameters<SetAllCookies>[0]) {
          // Renewed session cookies go both downstream and to the browser.
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          response = next();
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const { user } = await renewSession(supabase.auth);
  if (!user) {
    const loginUrl = new URL("/login", config.publicOrigin);
    loginUrl.searchParams.set(
      "next",
      `${request.nextUrl.pathname}${request.nextUrl.search}`,
    );
    const redirect = NextResponse.redirect(loginUrl);
    redirect.headers.set("content-security-policy", contentSecurityPolicy);
    return redirect;
  }

  return response;
}

export const config = {
  // Every page needs its own CSP nonce; static assets do not.
  matcher: [{ source: "/((?!_next/static|_next/image|icon\\.svg).*)" }],
};
