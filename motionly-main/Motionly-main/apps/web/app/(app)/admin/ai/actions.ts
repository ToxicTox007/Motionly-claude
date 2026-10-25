"use server";

import { revalidatePath } from "next/cache";
import { getAdminApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";
import type { RouteTestResult } from "@/lib/admin-inference";

// All mutations run on the server with the admin's token. A secret value is
// read from the submitted FormData, forwarded once to the API, and never
// returned to the browser or placed in a server-component prop.

const PREFIX = "/admin/inference";

type ActionState = {
  ok: boolean;
  message: string;
  warnings?: string[];
  errors?: string[];
};

async function adminFetch(
  path: string,
  init: { method: string; body?: unknown },
): Promise<Response> {
  const context = await getAdminApiContext();
  if (!context) throw new Error("Admin access is required.");
  return fetch(`${env.apiBaseUrl}${path}`, {
    method: init.method,
    cache: "no-store",
    headers: {
      ...context.headers,
      ...(init.body !== undefined
        ? { "content-type": "application/json" }
        : {}),
      ...(process.env.ADMIN_API_TOKEN
        ? { "x-motionly-admin-token": process.env.ADMIN_API_TOKEN }
        : {}),
    },
    ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
  });
}

function parseRecord(raw: string): Record<string, unknown> {
  const parsed = JSON.parse(raw) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("The record must be a JSON object.");
  }
  const record = parsed as Record<string, unknown>;
  if (typeof record.id !== "string" || !record.id.trim()) {
    throw new Error("The record must include a non-empty string id.");
  }
  return record;
}

function diagnostics(body: unknown): { warnings: string[]; errors: string[] } {
  const value = (body ?? {}) as {
    warnings?: Array<{ code: string; message: string }>;
    errors?: Array<{ code: string; message: string }>;
  };
  const toText = (list?: Array<{ code: string; message: string }>) =>
    (list ?? []).map((d) => `${d.code}: ${d.message}`);
  return { warnings: toText(value.warnings), errors: toText(value.errors) };
}

async function upsert(
  kind: "adapters" | "models" | "routes",
  formData: FormData,
): Promise<ActionState> {
  let record: Record<string, unknown>;
  try {
    record = parseRecord(String(formData.get("record") ?? ""));
  } catch (error) {
    return { ok: false, message: (error as Error).message };
  }
  const existing = String(formData.get("existingId") ?? "").trim();
  const path = existing
    ? `${PREFIX}/${kind}/${encodeURIComponent(existing)}`
    : `${PREFIX}/${kind}`;
  const method = existing ? "PUT" : "POST";
  const response = await adminFetch(path, { method, body: { record } });
  const body = await response.json().catch(() => ({}));
  const { warnings, errors } = diagnostics(body);
  const committed = Boolean((body as { committed?: boolean }).committed);
  revalidatePath("/admin/ai");
  if (!response.ok || !committed) {
    return {
      ok: false,
      message: errors.length
        ? "The change was rejected by validation."
        : "The change was not committed.",
      warnings,
      errors,
    };
  }
  return {
    ok: true,
    message: `Saved ${kind.slice(0, -1)} "${record.id}".`,
    warnings,
  };
}

export async function saveAdapter(formData: FormData): Promise<ActionState> {
  return upsert("adapters", formData);
}

export async function saveModel(formData: FormData): Promise<ActionState> {
  return upsert("models", formData);
}

export async function saveRoute(formData: FormData): Promise<ActionState> {
  return upsert("routes", formData);
}

export async function deleteEntry(formData: FormData): Promise<ActionState> {
  const kind = String(formData.get("kind") ?? "");
  const id = String(formData.get("id") ?? "").trim();
  if (!["adapters", "models", "routes"].includes(kind) || !id) {
    return { ok: false, message: "A kind and id are required." };
  }
  const response = await adminFetch(
    `${PREFIX}/${kind}/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
  const body = await response.json().catch(() => ({}));
  const { warnings, errors } = diagnostics(body);
  revalidatePath("/admin/ai");
  if (!response.ok || !(body as { committed?: boolean }).committed) {
    return {
      ok: false,
      message: "The delete was refused (it may be in use or managed by file).",
      warnings,
      errors,
    };
  }
  return { ok: true, message: `Deleted ${id}.`, warnings };
}

export async function createSecret(formData: FormData): Promise<ActionState> {
  const name = String(formData.get("name") ?? "").trim();
  const scope = String(formData.get("scope") ?? "global").trim() || "global";
  const value = String(formData.get("value") ?? "");
  if (!name || !value) {
    return { ok: false, message: "A secret name and value are required." };
  }
  // The value is forwarded exactly once and is never returned to the browser.
  const response = await adminFetch(`${PREFIX}/secrets`, {
    method: "POST",
    body: { name, scope, value },
  });
  revalidatePath("/admin/ai");
  if (!response.ok) {
    return { ok: false, message: "The secret could not be stored." };
  }
  return { ok: true, message: `Stored secret "${name}" (${scope}).` };
}

export async function rotateSecret(formData: FormData): Promise<ActionState> {
  const name = String(formData.get("name") ?? "").trim();
  const scope = String(formData.get("scope") ?? "global").trim() || "global";
  const value = String(formData.get("value") ?? "");
  if (!name || !value) {
    return { ok: false, message: "A secret name and new value are required." };
  }
  const response = await adminFetch(
    `${PREFIX}/secrets/${encodeURIComponent(name)}/rotate`,
    { method: "POST", body: { scope, value } },
  );
  revalidatePath("/admin/ai");
  if (!response.ok) {
    return { ok: false, message: "The secret could not be rotated." };
  }
  return { ok: true, message: `Rotated secret "${name}".` };
}

export async function deleteSecret(formData: FormData): Promise<ActionState> {
  const name = String(formData.get("name") ?? "").trim();
  const scope = String(formData.get("scope") ?? "global").trim() || "global";
  if (!name) return { ok: false, message: "A secret name is required." };
  const response = await adminFetch(
    `${PREFIX}/secrets/${encodeURIComponent(name)}?scope=${encodeURIComponent(scope)}`,
    { method: "DELETE" },
  );
  revalidatePath("/admin/ai");
  if (!response.ok) {
    return { ok: false, message: "The secret could not be deleted." };
  }
  return { ok: true, message: `Deleted secret "${name}".` };
}

export type RouteTestActionState = {
  ok: boolean;
  message: string;
  result?: RouteTestResult;
};

export async function testRoute(
  formData: FormData,
): Promise<RouteTestActionState> {
  const routeId = String(formData.get("routeId") ?? "").trim();
  if (!routeId) return { ok: false, message: "A route id is required." };
  const allowPaid = String(formData.get("allowPaid") ?? "") === "true";
  const maxCostRaw = Number(formData.get("maxCostUsd"));
  const maxCost =
    Number.isFinite(maxCostRaw) && maxCostRaw >= 0 && maxCostRaw <= 100
      ? maxCostRaw
      : 0.5;
  const response = await adminFetch(
    `${PREFIX}/routes/${encodeURIComponent(routeId)}/test`,
    { method: "POST", body: { max_cost_usd: maxCost, allow_paid: allowPaid } },
  );
  const body = await response.json().catch(() => null);
  revalidatePath("/admin/ai");
  if (!response.ok || !body) {
    return { ok: false, message: "The route test could not run." };
  }
  const result = body as RouteTestResult;
  if (result.paid_confirmation_required) {
    return {
      ok: false,
      message:
        "This paid async route needs explicit confirmation. Enable \u201callow paid\u201d and re-run.",
      result,
    };
  }
  return {
    ok: result.status === "ok",
    message: `Route test ${result.status} (${result.latency_ms ?? "?"} ms).`,
    result,
  };
}
