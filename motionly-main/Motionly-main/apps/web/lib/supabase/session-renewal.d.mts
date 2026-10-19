import type { User } from "@supabase/supabase-js";

export const SESSION_RENEWAL_MARGIN_MS: number;

interface RenewableAuth {
  getSession(): Promise<{ data: { session: { expires_at?: number } | null } }>;
  refreshSession(): Promise<unknown>;
  getUser(): Promise<{ data: { user: User | null } }>;
}

export function renewSession(
  auth: RenewableAuth,
  now?: number,
): Promise<{ user: User | null }>;
