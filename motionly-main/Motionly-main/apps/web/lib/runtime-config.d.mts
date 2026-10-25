export type Deployment =
  | "production"
  | "cpu-stage"
  | "test"
  | "development"
  | "local";

export type ServerConfig = {
  deployment: Deployment;
  publicOrigin: string;
  apiInternalUrl: string;
  supabaseUrl: string;
  supabaseAnonKey: string;
  billingEnabled: boolean;
  billingMode: "disabled" | "demo";
  skipAuthMiddleware: boolean;
};

export type PublicConfig = {
  publicOrigin: string;
  supabaseAnonKey: string;
  billingEnabled: boolean;
};

export type AuthCookieOptions = {
  name: string;
  path: string;
  sameSite: "lax";
  secure: boolean;
};

export const AUTH_COOKIE_NAME: string;
export const PUBLIC_CONFIG_ELEMENT_ID: string;

export function parseBooleanFlag(
  name: string,
  raw: string | undefined,
): boolean;
export function parseServerConfig(
  environment: Record<string, string | undefined>,
): ServerConfig;
export function publicConfig(config: ServerConfig): PublicConfig;
export function serializePublicConfig(config: PublicConfig): string;
export function parsePublicConfig(serialized: string): PublicConfig;
export function browserSupabaseUrl(pageOrigin: string): string;
export function authCookieOptions(siteOrigin: string): AuthCookieOptions;
