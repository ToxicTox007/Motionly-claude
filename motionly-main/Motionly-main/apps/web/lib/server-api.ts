import { env } from "@/lib/env";

// The one way server code (pages, server actions, route handlers) calls the
// API. It never throws for an API or network failure: every outcome comes back
// as a typed result, so a page can show an error with Retry instead of falling
// through to the global error screen or rendering a failure as empty data.

export type ServerApiFailureKind = "http" | "network" | "timeout" | "parse";

export type ServerApiSuccess<T> = { ok: true; status: number; data: T };

export type ServerApiFailure = {
  ok: false;
  kind: ServerApiFailureKind;
  /** HTTP status, when a response arrived. */
  status?: number;
  /** The API's machine code from the `{ error: { code } }` envelope. */
  code?: string;
  requestId?: string;
};

export type ServerApiResult<T> = ServerApiSuccess<T> | ServerApiFailure;

export type ServerApiOptions = {
  headers?: Record<string, string>;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** Serialized as JSON when present. */
  body?: unknown;
  timeoutMs?: number;
};

export const DEFAULT_SERVER_API_TIMEOUT_MS = 8000;

/**
 * Calls the API over the private network and returns a typed result.
 *
 * - `ok: true` carries the parsed JSON body (`null` for an empty body).
 * - `kind: "http"`: the API answered with a non-2xx status (its error code is
 *   read from the standard envelope when the body is JSON).
 * - `kind: "parse"`: a 2xx answer whose body is not JSON.
 * - `kind: "timeout"` / `"network"`: no usable answer arrived.
 */
export async function serverApiFetch<T>(
  path: string,
  {
    headers = {},
    method = "GET",
    body,
    timeoutMs = DEFAULT_SERVER_API_TIMEOUT_MS,
  }: ServerApiOptions = {},
): Promise<ServerApiResult<T>> {
  if (typeof window !== "undefined") {
    throw new Error("serverApiFetch is server-only.");
  }
  if (!path.startsWith("/")) {
    throw new Error("serverApiFetch takes an API path such as /projects.");
  }
  const requestHeaders: Record<string, string> = { ...headers };
  if (body !== undefined) requestHeaders["content-type"] = "application/json";

  let response: Response;
  let text: string;
  try {
    response = await fetch(`${env.apiBaseUrl.replace(/\/$/, "")}${path}`, {
      method,
      headers: requestHeaders,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
    text = await response.text();
  } catch (error) {
    return { ok: false, kind: isTimeout(error) ? "timeout" : "network" };
  }

  let payload: unknown = null;
  let parsed = true;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      parsed = false;
    }
  }

  if (!response.ok) {
    const envelope = parsed ? readEnvelope(payload) : {};
    return {
      ok: false,
      kind: "http",
      status: response.status,
      ...envelope,
    };
  }
  if (!parsed) return { ok: false, kind: "parse", status: response.status };
  return { ok: true, status: response.status, data: payload as T };
}

function isTimeout(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.name === "TimeoutError" || error.name === "AbortError")
  );
}

function readEnvelope(payload: unknown): {
  code?: string;
  requestId?: string;
} {
  if (!payload || typeof payload !== "object") return {};
  const error = (payload as { error?: unknown }).error;
  if (typeof error === "string") return { code: error };
  if (!error || typeof error !== "object") return {};
  const { code, request_id: requestId } = error as {
    code?: unknown;
    request_id?: unknown;
  };
  return {
    ...(typeof code === "string" ? { code } : {}),
    ...(typeof requestId === "string" ? { requestId } : {}),
  };
}

/** An API error code safe to put in a redirect query (letters, digits, _). */
export function failureCode(
  failure: ServerApiFailure,
  fallback: string,
): string {
  if (failure.kind === "timeout") return "timeout";
  if (failure.kind === "network") return "network_error";
  return failure.code && /^[a-z0-9_]{1,64}$/.test(failure.code)
    ? failure.code
    : fallback;
}
