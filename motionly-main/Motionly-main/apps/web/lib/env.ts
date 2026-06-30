import { z } from "zod";

const appEnvSchema = z.object({
  NEXT_PUBLIC_APP_URL: z.string().url(),
  NEXT_PUBLIC_API_BASE_URL: z.string().url().optional(),
  NEXT_PUBLIC_API_URL: z.string().url().optional(),
  NEXT_PUBLIC_SUPABASE_URL: z.string().url().optional(),
  NEXT_PUBLIC_SUPABASE_ANON_KEY: z.string().min(1).optional(),
  SUPABASE_URL: z.string().url().optional(),
  SUPABASE_ANON_KEY: z.string().min(1).optional(),
  NEXT_PUBLIC_ENABLE_GOOGLE_OAUTH: z.coerce.boolean().default(false),
  NEXT_PUBLIC_BILLING_ENABLED: z.coerce.boolean().default(false),
});

const rawEnv = {
  NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
  NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  SUPABASE_URL: process.env.SUPABASE_URL,
  SUPABASE_ANON_KEY: process.env.SUPABASE_ANON_KEY,
  NEXT_PUBLIC_ENABLE_GOOGLE_OAUTH: process.env.NEXT_PUBLIC_ENABLE_GOOGLE_OAUTH,
  NEXT_PUBLIC_BILLING_ENABLED: process.env.NEXT_PUBLIC_BILLING_ENABLED,
};

const isProductionBuild = process.env.NEXT_PHASE === "phase-production-build";
const parsedEnv = appEnvSchema.parse(
  isProductionBuild
    ? {
        NEXT_PUBLIC_APP_URL: rawEnv.NEXT_PUBLIC_APP_URL ?? "https://build.motionly.invalid",
        NEXT_PUBLIC_API_BASE_URL: rawEnv.NEXT_PUBLIC_API_BASE_URL,
        NEXT_PUBLIC_API_URL: rawEnv.NEXT_PUBLIC_API_URL ?? "https://api.build.motionly.invalid",
        NEXT_PUBLIC_SUPABASE_URL: rawEnv.NEXT_PUBLIC_SUPABASE_URL,
        NEXT_PUBLIC_SUPABASE_ANON_KEY: rawEnv.NEXT_PUBLIC_SUPABASE_ANON_KEY,
        SUPABASE_URL: rawEnv.SUPABASE_URL ?? "https://build.supabase.co",
        SUPABASE_ANON_KEY: rawEnv.SUPABASE_ANON_KEY ?? "build-placeholder",
        NEXT_PUBLIC_ENABLE_GOOGLE_OAUTH: rawEnv.NEXT_PUBLIC_ENABLE_GOOGLE_OAUTH,
        NEXT_PUBLIC_BILLING_ENABLED: rawEnv.NEXT_PUBLIC_BILLING_ENABLED,
      }
    : rawEnv,
);

if (process.env.NODE_ENV === "production" && !isProductionBuild) {
  const skipAuth = process.env.SKIP_AUTH_MIDDLEWARE;
  const skipAuthPublic = process.env.NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE;
  if (
    (skipAuth && ["true", "1", "yes", "on"].includes(skipAuth.toLowerCase())) ||
    (skipAuthPublic && ["true", "1", "yes", "on"].includes(skipAuthPublic.toLowerCase()))
  ) {
    throw new Error("Security violation: SKIP_AUTH_MIDDLEWARE and NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE are not allowed in production.");
  }

  const finalSupabaseAnonKey = parsedEnv.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? parsedEnv.SUPABASE_ANON_KEY;
  if (!finalSupabaseAnonKey || finalSupabaseAnonKey === "build-placeholder" || finalSupabaseAnonKey === "replace-with-supabase-anon-key") {
    throw new Error("Security violation: A valid production SUPABASE_ANON_KEY is required in production runtime.");
  }

  for (const [name, value] of [
    ["NEXT_PUBLIC_APP_URL", parsedEnv.NEXT_PUBLIC_APP_URL],
    ["NEXT_PUBLIC_API_BASE_URL or NEXT_PUBLIC_API_URL", parsedEnv.NEXT_PUBLIC_API_BASE_URL ?? parsedEnv.NEXT_PUBLIC_API_URL],
    ["SUPABASE_URL", parsedEnv.NEXT_PUBLIC_SUPABASE_URL ?? parsedEnv.SUPABASE_URL],
  ] as const) {
    if (!value) {
      throw new Error(`${name} is required.`);
    }
    if (/(localhost|127\.0\.0\.1|0\.0\.0\.0|\.invalid)/i.test(value)) {
      throw new Error(`${name} must be a deployed production URL.`);
    }
  }
}

const isClient = typeof window !== "undefined";

const supabaseUrl = isClient
  ? "/supabase"
  : (parsedEnv.NEXT_PUBLIC_SUPABASE_URL ?? parsedEnv.SUPABASE_URL);

const supabaseAnonKey = parsedEnv.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? parsedEnv.SUPABASE_ANON_KEY;

const apiBaseUrl = isClient
  ? "/api"
  : (parsedEnv.NEXT_PUBLIC_API_BASE_URL ?? parsedEnv.NEXT_PUBLIC_API_URL);

if (!apiBaseUrl) {
  throw new Error("NEXT_PUBLIC_API_BASE_URL or NEXT_PUBLIC_API_URL is required.");
}

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error("Supabase public URL and anon key are required.");
}

export const env = {
  appUrl: parsedEnv.NEXT_PUBLIC_APP_URL,
  apiBaseUrl,
  supabaseUrl,
  supabaseAnonKey,
  googleOAuthEnabled: parsedEnv.NEXT_PUBLIC_ENABLE_GOOGLE_OAUTH,
  billingEnabled: parsedEnv.NEXT_PUBLIC_BILLING_ENABLED,
};

export const isSupabaseConfigured = Boolean(
  (rawEnv.NEXT_PUBLIC_SUPABASE_URL ?? rawEnv.SUPABASE_URL) &&
    (rawEnv.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? rawEnv.SUPABASE_ANON_KEY),
);

export function resolveProxiedUrl(url: string | null | undefined): string | null | undefined {
  if (!url) return url;
  try {
    const parsed = new URL(url);
    if (
      parsed.port === "54321" &&
      (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost" || parsed.hostname === "supabase")
    ) {
      return `/supabase${parsed.pathname}${parsed.search}`;
    }
    if (
      parsed.port === "8000" &&
      (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost" || parsed.hostname === "api")
    ) {
      return `/api${parsed.pathname}${parsed.search}`;
    }
  } catch {
    // Relative or invalid URL
  }
  return url;
}

