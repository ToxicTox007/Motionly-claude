import { env } from "@/lib/env";

/**
 * One data processor the operator's inference registry declares for its enabled
 * remote routes (plan Task 9A). The API returns display metadata only — no base
 * URL, model id, route id, provider label or secret — so the privacy page never
 * hard-codes a processor name.
 */
export type DataProcessor = {
  display_name: string;
  purpose: string;
  data_categories: string[];
  policy_url: string;
};

type DataProcessorsResponse = {
  processors?: unknown;
};

function isDataProcessor(value: unknown): value is DataProcessor {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.display_name === "string" &&
    typeof entry.purpose === "string" &&
    typeof entry.policy_url === "string" &&
    Array.isArray(entry.data_categories) &&
    entry.data_categories.every((item) => typeof item === "string")
  );
}

/**
 * Fetch the configured data processors from the public API, server-side.
 *
 * Follows the server-side public-fetch pattern used elsewhere (``env.apiBaseUrl``
 * + ``cache: "no-store"``). Any failure — the API being unreachable, a non-OK
 * status, or a malformed body — resolves to an empty list so the privacy page
 * degrades to a neutral fallback message rather than crashing.
 */
export async function fetchDataProcessors(): Promise<DataProcessor[]> {
  let response: Response;
  try {
    response = await fetch(`${env.apiBaseUrl}/public/data-processors`, {
      cache: "no-store",
      headers: { accept: "application/json" },
    });
  } catch {
    return [];
  }
  if (!response.ok) return [];
  let payload: DataProcessorsResponse;
  try {
    payload = (await response.json()) as DataProcessorsResponse;
  } catch {
    return [];
  }
  const processors = payload?.processors;
  if (!Array.isArray(processors)) return [];
  return processors.filter(isDataProcessor);
}
