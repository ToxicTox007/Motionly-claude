// Shared types and pure helpers for the admin AI configuration UI (plan Task
// 23, Section 2.7). These mirror the admin inference API schemas in
// apps/api/app/schemas/admin_inference.py. No secret value is ever modelled
// here: a secret is metadata-only on read and write-only on mutate.

export type RegistryManaged = "file" | "database";

export type ValidationDiagnostic = {
  object_kind: string;
  object_id: string;
  severity: "error" | "warning";
  code: string;
  message: string;
  path: string;
};

export type RegistryEntry = {
  id: string;
  managed: RegistryManaged;
  record: Record<string, unknown>;
};

export type RegistrySnapshot = {
  database_enabled: boolean;
  registry_version: number;
  adapters: RegistryEntry[];
  models: RegistryEntry[];
  routes: RegistryEntry[];
  warnings: ValidationDiagnostic[];
  errors: ValidationDiagnostic[];
};

// A secret read returns metadata only — never a value, ciphertext or KEK.
export type SecretMetadata = {
  name: string;
  scope: string;
  fingerprint: string;
  key_version: string;
  created_by?: string | null;
  created_at?: string | null;
  rotated_at?: string | null;
  last_used_at?: string | null;
};

export type RouteHealthEntry = {
  route_id: string;
  capability?: string | null;
  model?: string | null;
  model_revision?: string | null;
  enabled: boolean;
  managed: RegistryManaged;
  circuit_state: string;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  p50_latency_ms?: number | null;
  p95_latency_ms?: number | null;
  error_rate?: number | null;
  total_calls: number;
  fallback_count: number;
  last_error_code?: string | null;
  hard_cap_max_requests?: number | null;
  hard_cap_max_units?: number | null;
  pricing_status: string;
};

export type RouteHealth = {
  database_enabled: boolean;
  routes: RouteHealthEntry[];
};

export type RouteTestResult = {
  route_id: string;
  capability: string;
  status: "ok" | "schema_invalid" | "error";
  schema_valid: boolean;
  latency_ms?: number | null;
  usage: Record<string, number>;
  estimated_cost_usd: number;
  actual_cost_usd: number;
  cost_unknown: boolean;
  error_code?: string | null;
  circuit_state: string;
  paid_confirmation_required: boolean;
  operation_id?: string | null;
};

// One operator-facing warning badge. `tone` never stands alone: the UI always
// renders the visible `label` text as well, so meaning is never carried by
// colour only (WCAG 1.4.1).
export type WarningBadge = {
  code: string;
  label: string;
  tone: "warning" | "danger" | "info";
};

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

// Derive the Section 2.7 warning badges for a model record. Pure and
// provider-neutral: it inspects declared metadata fields only, never a name.
export function modelWarningBadges(
  record: Record<string, unknown>,
): WarningBadge[] {
  const badges: WarningBadge[] = [];
  const revision = asString(record.revision ?? record.sha);
  if (revision.toLowerCase() === "provider-managed") {
    badges.push({
      code: "provider_managed_revision",
      label: "Provider-managed revision",
      tone: "info",
    });
  }
  const license = asRecord(record.license);
  const licenseId = asString(record.license_id ?? license.id ?? license.spdx);
  const commercial = (record.commercial_use ??
    license.commercial_use) as unknown;
  if (!licenseId) {
    badges.push({
      code: "missing_remote_license",
      label: "Missing licence metadata",
      tone: "warning",
    });
  }
  if (commercial === false) {
    badges.push({
      code: "noncommercial_license",
      label: "Non-commercial licence",
      tone: "danger",
    });
  }
  return badges;
}

// Derive the Section 2.7 warning badges for a route record.
export function routeWarningBadges(
  record: Record<string, unknown>,
): WarningBadge[] {
  const badges: WarningBadge[] = [];
  const pricingStatus = asString(record.pricing_status);
  const hasHardCap =
    record.hard_cap_max_requests != null || record.hard_cap_max_units != null;
  if (pricingStatus && pricingStatus !== "priced") {
    badges.push({
      code: "unknown_pricing",
      label: "Unknown pricing",
      tone: "warning",
    });
  }
  if (!hasHardCap && pricingStatus && pricingStatus !== "priced") {
    badges.push({
      code: "no_hard_cap",
      label: "No hard cap",
      tone: "warning",
    });
  }
  const dataProcessor = asString(
    record.data_processor ?? asRecord(record.compliance).data_processor,
  );
  if (!dataProcessor) {
    badges.push({
      code: "missing_data_processor",
      label: "Missing data-processor metadata",
      tone: "warning",
    });
  }
  return badges;
}

// Derive badges from a route-health row (provider-managed revision, unknown
// pricing, missing hard cap) so the health panel can flag the live route too.
export function routeHealthBadges(entry: RouteHealthEntry): WarningBadge[] {
  const badges: WarningBadge[] = [];
  if (asString(entry.model_revision).toLowerCase() === "provider-managed") {
    badges.push({
      code: "provider_managed_revision",
      label: "Provider-managed revision",
      tone: "info",
    });
  }
  if (entry.pricing_status && entry.pricing_status !== "priced") {
    badges.push({
      code: "unknown_pricing",
      label: "Unknown pricing",
      tone: "warning",
    });
    if (
      entry.hard_cap_max_requests == null &&
      entry.hard_cap_max_units == null
    ) {
      badges.push({
        code: "no_hard_cap",
        label: "No hard cap",
        tone: "warning",
      });
    }
  }
  return badges;
}

// Order routes by their declared priority (ascending; lower runs first) so the
// keyboard reordering control and the fallback list share one stable order.
export function sortRoutesByPriority(
  entries: RegistryEntry[],
): RegistryEntry[] {
  return [...entries].sort((a, b) => {
    const pa = Number(a.record.priority ?? Number.MAX_SAFE_INTEGER);
    const pb = Number(b.record.priority ?? Number.MAX_SAFE_INTEGER);
    if (pa !== pb) return pa - pb;
    return a.id.localeCompare(b.id);
  });
}

// Parse a free-form JSON record from a textarea, returning either the object or
// a human-readable error. Used by the adapter/model/route forms.
export function parseRecordJson(
  text: string,
):
  | { ok: true; record: Record<string, unknown> }
  | { ok: false; error: string } {
  const trimmed = text.trim();
  if (!trimmed)
    return { ok: false, error: "Enter a JSON object for the record." };
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (error) {
    return { ok: false, error: `Invalid JSON: ${(error as Error).message}` };
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, error: "The record must be a JSON object." };
  }
  const record = parsed as Record<string, unknown>;
  if (!asString(record.id)) {
    return {
      ok: false,
      error: "The record must include a non-empty string id.",
    };
  }
  return { ok: true, record };
}
