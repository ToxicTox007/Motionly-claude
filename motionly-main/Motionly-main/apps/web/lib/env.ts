import {
  PUBLIC_CONFIG_ELEMENT_ID,
  browserSupabaseUrl,
  parsePublicConfig,
  parseServerConfig,
  publicConfig,
  type PublicConfig,
  type ServerConfig,
} from "./runtime-config.mjs";

// Configuration is read when it is first used, never at build time (see
// runtime-config.mjs). The server reads its environment once per process; the
// browser reads the public values the root layout embeds in every page.

let serverConfig: ServerConfig | undefined;
let browserConfig: PublicConfig | undefined;

const inBrowser = () => typeof window !== "undefined";

export function getServerConfig(): ServerConfig {
  if (inBrowser()) {
    throw new Error("Server configuration is not available in the browser.");
  }
  serverConfig ??= parseServerConfig(process.env);
  return serverConfig;
}

export function getPublicConfig(): PublicConfig {
  if (!inBrowser()) return publicConfig(getServerConfig());
  browserConfig ??= parsePublicConfig(
    document.getElementById(PUBLIC_CONFIG_ELEMENT_ID)?.textContent ?? "",
  );
  return browserConfig;
}

/**
 * Service locations for the code that is running: the browser uses the
 * public origin's /api and /supabase paths; the server uses the private
 * network.
 */
export const env = {
  get appUrl(): string {
    return getPublicConfig().publicOrigin;
  },
  get apiBaseUrl(): string {
    return inBrowser() ? "/api" : getServerConfig().apiInternalUrl;
  },
  get supabaseUrl(): string {
    return inBrowser()
      ? browserSupabaseUrl(window.location.origin)
      : getServerConfig().supabaseUrl;
  },
  get supabaseAnonKey(): string {
    return getPublicConfig().supabaseAnonKey;
  },
  get billingEnabled(): boolean {
    return getPublicConfig().billingEnabled;
  },
  /** Server only: "demo" when the billing surface is the no-charge demo gateway. */
  get billingMode(): "disabled" | "demo" {
    return getServerConfig().billingMode;
  },
  /** Local development only; the config refuses it anywhere else. */
  get skipAuthMiddleware(): boolean {
    return !inBrowser() && getServerConfig().skipAuthMiddleware;
  },
};
