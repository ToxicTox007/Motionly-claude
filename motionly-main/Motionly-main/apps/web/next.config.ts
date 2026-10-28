import type { NextConfig } from "next";
import { MONOREPO_ROOT } from "./lib/monorepo-root.mjs";
import { parseServerConfig } from "./lib/runtime-config.mjs";

// Phase names from next/constants (compared directly so this file also loads
// under plain Node in the unit tests).
const DEVELOPMENT_SERVER = "phase-development-server";
const PRODUCTION_SERVER = "phase-production-server";

// Static headers only. The Content-Security-Policy needs a per-request nonce,
// so proxy.ts sets it (lib/security-headers.mjs).
const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  },
];

export default function nextConfig(phase: string): NextConfig {
  const development = phase === DEVELOPMENT_SERVER;
  // A server validates its runtime configuration when it starts, so a
  // misconfigured container exits instead of failing requests. A build needs
  // no configuration: the image is configured where it runs. (The image's
  // standalone server does not run this file; instrumentation.ts does the
  // same check there.)
  const config =
    development || phase === PRODUCTION_SERVER
      ? parseServerConfig(process.env)
      : undefined;

  return {
    poweredByHeader: false,
    reactStrictMode: true,
    // The image ships .next/standalone: server.js and only the node_modules
    // files it uses, traced from the monorepo root (FE-42).
    output: "standalone",
    outputFileTracingRoot: MONOREPO_ROOT,
    ...(development
      ? {
          allowedDevOrigins: ["localhost", "127.0.0.1", "*.app.github.dev"],
          experimental: {
            serverActions: {
              allowedOrigins: [
                "localhost:3000",
                "127.0.0.1:3000",
                "*.app.github.dev",
              ],
            },
          },
        }
      : {}),
    async rewrites() {
      // Deployed, the edge (Caddy behind CloudFront) routes /api to FastAPI
      // and /supabase to Kong. `next dev` stands in for it locally.
      if (!development || !config) return [];
      return [
        {
          source: "/api/:path*",
          destination: `${config.apiInternalUrl}/:path*`,
        },
        {
          source: "/supabase/:path*",
          destination: `${config.supabaseUrl}/:path*`,
        },
      ];
    },
    async headers() {
      return [
        {
          source: "/:path*",
          headers: securityHeaders,
        },
        {
          source: "/icon.svg",
          headers: [
            {
              key: "Cache-Control",
              value: "public, max-age=86400, stale-while-revalidate=604800",
            },
          ],
        },
        // Admin screens carry account-level data (emails, ban state, one-time
        // recovery links in page state): never cache them, and never send
        // their URLs to another page in a Referer. Listed after the global
        // rule so its Referrer-Policy is the one that applies here.
        {
          source: "/admin/:path*",
          headers: [
            { key: "Cache-Control", value: "no-store" },
            { key: "Referrer-Policy", value: "no-referrer" },
          ],
        },
      ];
    },
  };
}
