import { env } from "@/lib/env";

/**
 * An absolute URL on the public origin (PUBLIC_ORIGIN).
 *
 * Use this, not request.url or request headers, for redirects and links sent
 * to users: behind the edge, Next.js sees its own listen address
 * (http://localhost:3000) as the request URL, and Origin/X-Forwarded-* headers
 * are whatever the viewer sent.
 */
export function publicUrl(path: string): URL {
  return new URL(path, env.appUrl);
}
