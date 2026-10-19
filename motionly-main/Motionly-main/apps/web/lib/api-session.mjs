// Authenticates browser calls to the API with the browser's own Supabase
// session (N4). The session client renews its token before it expires, so
// each call reads the current token rather than a copy taken when the page
// loaded. If the API still rejects a token (renewed elsewhere, or expired
// while the tab slept), the session is renewed and the call retried once.
export class SessionExpiredError extends Error {
  constructor() {
    super("Your session expired. Sign in again before continuing.");
    this.name = "SessionExpiredError";
  }
}

async function currentToken(auth) {
  const {
    data: { session },
  } = await auth.getSession();
  if (!session?.access_token) throw new SessionExpiredError();
  return session.access_token;
}

function headersFor(token, { workspaceId, json }) {
  return {
    ...(json ? { "content-type": "application/json" } : {}),
    authorization: `Bearer ${token}`,
    "x-motionly-workspace-id": workspaceId,
  };
}

function plainHeaders(headers) {
  if (!headers) return {};
  if (typeof headers.entries === "function" && !Array.isArray(headers)) {
    return Object.fromEntries(headers.entries());
  }
  return Array.isArray(headers) ? Object.fromEntries(headers) : { ...headers };
}

export async function fetchWithSession(
  auth,
  input,
  init = {},
  { workspaceId, json = false, fetch: fetchImpl = globalThis.fetch },
) {
  const extra = plainHeaders(init.headers);
  const send = (token) => {
    const { authorization, ...ownHeaders } = headersFor(token, {
      workspaceId,
      json,
    });
    return fetchImpl(input, {
      ...init,
      headers: { ...ownHeaders, ...extra, authorization },
    });
  };

  const token = await currentToken(auth);
  const response = await send(token);
  if (response.status !== 401) return response;

  const { data, error } = await auth.refreshSession();
  const renewed = data?.session?.access_token;
  if (error || !renewed || renewed === token) return response;
  return send(renewed);
}
