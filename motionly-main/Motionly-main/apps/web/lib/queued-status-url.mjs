const ABSOLUTE_HTTP_URL = /^https?:\/\//i;

/**
 * Converts an API-relative queued-job status path into a fetchable API URL.
 * Browser callers use a relative `/api` proxy while server callers may use an
 * absolute API base. An API response must never redirect authenticated polling
 * to an unrelated origin.
 */
export function normalizeQueuedLayerStatusUrl(statusUrl, apiBaseUrl) {
  const target = String(statusUrl ?? "").trim();
  const base = String(apiBaseUrl ?? "").trim();
  if (!target) throw new Error("Queued layer status URL must not be empty.");
  if (!base) throw new Error("Queued layer API base URL must not be empty.");
  if (target.startsWith("//")) {
    throw new Error("Queued layer status URL must not be protocol-relative.");
  }

  if (ABSOLUTE_HTTP_URL.test(target)) {
    if (!ABSOLUTE_HTTP_URL.test(base)) {
      throw new Error(
        "Absolute queued status URLs require an absolute API base URL.",
      );
    }
    const expectedApi = new URL(base);
    const requestedStatus = new URL(target);
    if (requestedStatus.origin !== expectedApi.origin) {
      throw new Error("Queued layer status URL must use the same API origin.");
    }
    return requestedStatus.toString();
  }

  const statusPath = target.startsWith("/") ? target : `/${target}`;
  return `${base.replace(/\/+$/, "")}${statusPath}`;
}
