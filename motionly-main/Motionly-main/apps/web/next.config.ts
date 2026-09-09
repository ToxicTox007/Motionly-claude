import type { NextConfig } from "next";

const appUrl = process.env.NEXT_PUBLIC_APP_URL;
const apiUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000";
const supabaseUrl =
  process.env.NEXT_PUBLIC_SUPABASE_URL ??
  process.env.SUPABASE_URL ??
  "http://127.0.0.1:54321";

const serverActionAllowedOrigins = [
  "localhost:3000",
  "127.0.0.1:3000",
  "*.app.github.dev",
  "*.vercel.app",
];

function originOf(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    return new URL(value).origin;
  } catch {
    return undefined;
  }
}

function websocketOrigin(value: string | undefined): string | undefined {
  const origin = originOf(value);
  if (!origin) return undefined;
  return origin.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
}

const configuredApiOrigin = originOf(apiUrl);
const configuredSupabaseOrigin = originOf(supabaseUrl);
const configuredSupabaseWebsocketOrigin = websocketOrigin(supabaseUrl);
const connectSources = Array.from(
  new Set(
    [
      "'self'",
      configuredApiOrigin,
      configuredSupabaseOrigin,
      configuredSupabaseWebsocketOrigin,
      "https:",
      "wss:",
    ].filter((source): source is string => Boolean(source)),
  ),
);

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "frame-src 'none'",
  "form-action 'self'",
  `script-src 'self' 'unsafe-inline'${
    process.env.NODE_ENV === "production" ? "" : " 'unsafe-eval'"
  }`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data: https:",
  `media-src ${[
    "'self'",
    "blob:",
    "data:",
    "https:",
    configuredApiOrigin,
    configuredSupabaseOrigin,
  ]
    .filter(Boolean)
    .join(" ")}`,
  "font-src 'self' data:",
  "worker-src 'self' blob:",
  `connect-src ${connectSources.join(" ")}`,
  "manifest-src 'self'",
  process.env.NODE_ENV === "production" ? "upgrade-insecure-requests" : "",
]
  .filter(Boolean)
  .join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  },
];

if (appUrl) {
  try {
    serverActionAllowedOrigins.push(new URL(appUrl).host);
  } catch {
    // Ignore invalid app URL here; runtime env validation handles hard failures.
  }
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  allowedDevOrigins: ["localhost", "127.0.0.1", "*.app.github.dev"],
  experimental: {
    serverActions: {
      allowedOrigins: serverActionAllowedOrigins,
    },
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiUrl}/:path*`,
      },
      {
        source: "/supabase/:path*",
        destination: `${supabaseUrl}/:path*`,
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
    ];
  },
};

export default nextConfig;
