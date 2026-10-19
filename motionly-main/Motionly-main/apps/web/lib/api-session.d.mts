export class SessionExpiredError extends Error {}

interface SessionAuth {
  getSession(): Promise<{
    data: { session: { access_token: string } | null };
  }>;
  refreshSession(): Promise<{
    data: { session: { access_token: string } | null };
    error: unknown;
  }>;
}

export interface ApiAuth {
  workspaceId: string;
  json?: boolean;
}

export function fetchWithSession(
  auth: SessionAuth,
  input: string,
  init: RequestInit,
  options: ApiAuth & { fetch?: typeof fetch },
): Promise<Response>;
