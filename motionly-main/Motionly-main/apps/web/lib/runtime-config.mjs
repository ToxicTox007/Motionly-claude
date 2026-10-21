// Runtime configuration for the web app.
//
// The web image is built once and configured when it starts. Next.js inlines
// every NEXT_PUBLIC_* variable into the bundles at build time, so none are
// used: the server reads the variables below from its environment, and the
// browser receives only `publicConfig()`, embedded in each page by the root
// layout. Browsers reach everything through one public origin:
//   <origin>/           this app
//   <origin>/api/       the FastAPI service
//   <origin>/supabase/  Supabase (Auth, REST, Storage)
// The server talks to the API and Supabase over the private network.

const DEPLOYMENTS = ["production", "cpu-stage", "test", "development", "local"];
const AUTH_BYPASS_DEPLOYMENTS = new Set(["development", "local"]);
// Deployment billing posture. Live/Stripe billing is being removed, so the only
// values are a fully disabled surface or an explicitly labelled no-charge demo.
const BILLING_MODES = new Set(["disabled", "demo"]);
const PLACEHOLDER_ANON_KEYS = new Set([
  "replace-with-supabase-anon-key",
  "build-placeholder",
]);
const LOCAL_HOSTNAME =
  /(^|\.)localhost$|^127(\.\d{1,3}){3}$|^0\.0\.0\.0$|^\[::1?\]$|\.invalid$/i;
const HTML_UNSAFE = /[<>&\u2028\u2029]/g;

/** One cookie name for every Supabase client, whatever URL it uses. */
export const AUTH_COOKIE_NAME = "sb-motionly-auth-token";
export const PUBLIC_CONFIG_ELEMENT_ID = "motionly-public-config";

function value(environment, name) {
  const raw = environment[name];
  // Compose passes unset variables as empty strings.
  return typeof raw === "string" && raw !== "" ? raw : undefined;
}

function required(environment, name) {
  const raw = value(environment, name);
  if (raw === undefined) throw new Error(`${name} is required.`);
  return raw;
}

/** Only the literals "true" and "false" are accepted; unset means false. */
export function parseBooleanFlag(name, raw) {
  if (raw === undefined || raw === "") return false;
  if (raw === "true") return true;
  if (raw === "false") return false;
  throw new Error(`${name} must be "true" or "false".`);
}

/**
 * The deployment billing posture. Production must state it explicitly; the
 * only values are "disabled" (no billing surface) and "demo" (a no-charge
 * surface). Non-production derives it from BILLING_ENABLED when unset.
 */
export function parseBillingMode(deployment, raw, billingEnabled) {
  if (raw === undefined) {
    if (deployment === "production") {
      throw new Error(
        'BILLING_MODE is required in production; set it to "disabled" or "demo".',
      );
    }
    return billingEnabled ? "demo" : "disabled";
  }
  if (!BILLING_MODES.has(raw)) {
    throw new Error('BILLING_MODE must be "disabled" or "demo".');
  }
  return raw;
}

function httpUrl(name, raw) {
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`${name} must be an http(s) URL.`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`${name} must be an http(s) URL.`);
  }
  return raw.replace(/\/+$/, "");
}

function origin(name, raw) {
  const message = `${name} must be an origin such as https://example.com (no path, query, or fragment).`;
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(message);
  }
  if (
    (url.protocol !== "http:" && url.protocol !== "https:") ||
    url.origin !== raw.replace(/\/$/, "")
  ) {
    throw new Error(message);
  }
  return url.origin;
}

function deploymentOf(environment) {
  const appEnv = value(environment, "APP_ENV");
  if (appEnv === undefined) {
    // Fail closed: a production Node runtime without APP_ENV is production.
    return environment.NODE_ENV === "production" ? "production" : "development";
  }
  if (!DEPLOYMENTS.includes(appEnv)) {
    throw new Error(`APP_ENV must be one of ${DEPLOYMENTS.join(", ")}.`);
  }
  return appEnv;
}

/** Validates the server's environment. Throws an Error naming the variable. */
export function parseServerConfig(environment) {
  const deployment = deploymentOf(environment);
  const publicOrigin = origin(
    "PUBLIC_ORIGIN",
    required(environment, "PUBLIC_ORIGIN"),
  );
  const apiInternalUrl = httpUrl(
    "API_INTERNAL_URL",
    required(environment, "API_INTERNAL_URL"),
  );
  const supabaseUrl = httpUrl(
    "SUPABASE_URL",
    required(environment, "SUPABASE_URL"),
  );
  const supabaseAnonKey = required(environment, "SUPABASE_ANON_KEY");
  const billingEnabled = parseBooleanFlag(
    "BILLING_ENABLED",
    value(environment, "BILLING_ENABLED"),
  );
  const billingMode = parseBillingMode(
    deployment,
    value(environment, "BILLING_MODE"),
    billingEnabled,
  );
  const skipAuthMiddleware = parseBooleanFlag(
    "SKIP_AUTH_MIDDLEWARE",
    value(environment, "SKIP_AUTH_MIDDLEWARE"),
  );

  if (skipAuthMiddleware && !AUTH_BYPASS_DEPLOYMENTS.has(deployment)) {
    throw new Error(
      "SKIP_AUTH_MIDDLEWARE is only allowed when APP_ENV is development or local.",
    );
  }
  if (deployment === "production") {
    const { protocol, hostname } = new URL(publicOrigin);
    if (protocol !== "https:" || LOCAL_HOSTNAME.test(hostname)) {
      throw new Error(
        "PUBLIC_ORIGIN must be the deployed HTTPS origin in production.",
      );
    }
    if (PLACEHOLDER_ANON_KEYS.has(supabaseAnonKey)) {
      throw new Error(
        "SUPABASE_ANON_KEY must be the deployment's anon key in production.",
      );
    }
  }

  return {
    deployment,
    publicOrigin,
    apiInternalUrl,
    supabaseUrl,
    supabaseAnonKey,
    billingEnabled,
    billingMode,
    skipAuthMiddleware,
  };
}

/** The only configuration the browser receives. */
export function publicConfig(config) {
  return {
    publicOrigin: config.publicOrigin,
    supabaseAnonKey: config.supabaseAnonKey,
    billingEnabled: config.billingEnabled,
  };
}

/** JSON that is safe inside a <script type="application/json"> element. */
export function serializePublicConfig(config) {
  return JSON.stringify(config).replace(
    HTML_UNSAFE,
    (character) =>
      `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
}

export function parsePublicConfig(serialized) {
  if (!serialized) {
    throw new Error("Motionly public configuration is missing from the page.");
  }
  let parsed;
  try {
    parsed = JSON.parse(serialized);
  } catch {
    throw new Error("Motionly public configuration is invalid.");
  }
  if (
    !parsed ||
    typeof parsed !== "object" ||
    typeof parsed.publicOrigin !== "string" ||
    typeof parsed.supabaseAnonKey !== "string" ||
    typeof parsed.billingEnabled !== "boolean"
  ) {
    throw new Error("Motionly public configuration is invalid.");
  }
  return {
    publicOrigin: parsed.publicOrigin,
    supabaseAnonKey: parsed.supabaseAnonKey,
    billingEnabled: parsed.billingEnabled,
  };
}

/** supabase-js needs an absolute URL; the edge routes /supabase to Kong. */
export function browserSupabaseUrl(pageOrigin) {
  return `${pageOrigin}/supabase`;
}

export function authCookieOptions(siteOrigin) {
  return {
    name: AUTH_COOKIE_NAME,
    path: "/",
    sameSite: "lax",
    secure: siteOrigin.startsWith("https:"),
  };
}
