"use client";

// Task 9B — capability state for the editor UI.
//
// This module fetches the public `GET /capabilities` contract through the
// existing API client and turns it into a small, reusable state the editor
// panels gate their optional controls on. A later task (Section 2.11, model
// pickers) reuses the same hook and helpers, so the shape here is deliberately
// capability-centric and carries no vendor identity.
//
// The contract is public and capability-level only: it exposes an id, a
// status, a machine-readable reason, the async flag, the compute requirement
// and a route count. It never returns a base URL, a model id, a provider label
// or a secret, and nothing here reintroduces one — the client state type below
// is a strict subset of that contract (see `tests/capability-gating.test.mjs`).

import type { ApiResponse } from "@motionly/shared";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";

/** The typed `GET /capabilities` response from the generated contract. */
export type CapabilitiesResponse = ApiResponse<"GET /capabilities">;

/** One capability summary from the contract. */
export type CapabilitySummary = CapabilitiesResponse["capabilities"][number];

/**
 * The availability a control reacts to. `available` and `degraded` come from
 * the API; `unknown` is the neutral state used when the API is unreachable, so
 * an optional control is never wrongly presented as configured and the editor
 * never crashes on a failed fetch.
 */
export type CapabilityAvailability =
  | "available"
  | "degraded"
  | "unavailable"
  | "unknown";

/**
 * The reusable per-capability state. It is a strict subset of the public
 * contract plus the derived `enabled`/`available` booleans the controls use.
 * It intentionally has no `base_url`, `model_id`, `url`, `secret` or provider
 * label — the same guarantee the API makes, re-asserted on the client so the
 * model-picker task can build on it safely.
 */
export type CapabilityState = {
  id: string;
  availability: CapabilityAvailability;
  reason: string | null;
  isAsync: boolean;
  computeRequirement: CapabilitySummary["compute_requirement"];
  routeCount: number;
  required: boolean;
  /** True when the control may be used (available or degraded). */
  enabled: boolean;
  /** True only when fully available (not degraded, unavailable or unknown). */
  available: boolean;
};

/** The whole capability catalogue, keyed by capability id, plus its version. */
export type CapabilityCatalog = {
  registryVersion: string | null;
  capabilities: Record<string, CapabilityState>;
  /** Neutral: the catalogue could not be fetched; treat optionals as unknown. */
  statusUnknown: boolean;
};

// Visible text. These are shown next to a gated control; state is never
// signalled by colour alone (plan Task 9B). They carry no vendor name.
export const CAPABILITY_UNAVAILABLE_TEXT =
  "Unavailable — not configured by your operator";
export const CAPABILITY_DEGRADED_TEXT = "Limited — running in degraded mode";

/** The known optional capability ids the editor gates controls on. */
export const CAPABILITY_IDS = {
  videoGeneration: "video.generation.submit",
  stemSeparation: "audio.stem_separation",
  objectDetection: "vision.object_detection",
  captions: "vision.caption",
  emotion: "audio.emotion",
  embeddingsText: "embeddings.text",
  embeddingsImage: "embeddings.image",
  matte: "matte.segmentation",
  diarization: "audio.diarization",
  imageGeneration: "image.generation",
} as const;

function toState(summary: CapabilitySummary): CapabilityState {
  const availability = summary.status as CapabilityAvailability;
  return {
    id: summary.id,
    availability,
    reason: summary.reason ?? null,
    isAsync: summary.is_async,
    computeRequirement: summary.compute_requirement,
    routeCount: summary.route_count,
    required: summary.required,
    enabled: availability === "available" || availability === "degraded",
    available: availability === "available",
  };
}

/** Build the keyed catalogue from a fetched response. */
export function catalogFromResponse(
  response: CapabilitiesResponse,
): CapabilityCatalog {
  const capabilities: Record<string, CapabilityState> = {};
  for (const summary of response.capabilities) {
    capabilities[summary.id] = toState(summary);
  }
  return {
    registryVersion: response.registry_version,
    capabilities,
    statusUnknown: false,
  };
}

/** The neutral catalogue used when the API cannot be reached. */
export function unknownCatalog(): CapabilityCatalog {
  return { registryVersion: null, capabilities: {}, statusUnknown: true };
}

/**
 * Resolve one capability to a stable state. A capability absent from the
 * catalogue — including the whole neutral `statusUnknown` case — resolves to a
 * neutral `unknown` optional state rather than throwing, so a control can
 * always render.
 */
export function resolveCapability(
  catalog: CapabilityCatalog,
  id: string,
): CapabilityState {
  const found = catalog.capabilities[id];
  if (found) return found;
  return {
    id,
    availability: catalog.statusUnknown ? "unknown" : "unavailable",
    reason: catalog.statusUnknown ? null : "not_configured",
    isAsync: false,
    computeRequirement: "any",
    routeCount: 0,
    required: false,
    enabled: false,
    available: false,
  };
}

/** The visible status text for a resolved capability, or null when available. */
export function capabilityStatusText(state: CapabilityState): string | null {
  if (state.availability === "degraded") return CAPABILITY_DEGRADED_TEXT;
  if (!state.enabled) return CAPABILITY_UNAVAILABLE_TEXT;
  return null;
}

/**
 * Accessibility/props for a control gated on a capability. Unavailable or
 * unknown controls stay in the tab order and are marked `aria-disabled`
 * (never the `disabled` attribute), so keyboard focus is preserved — the
 * codebase's focus pattern (see `lib/useFocusTrap.ts`, which skips only
 * `[disabled]` and `tabindex="-1"`). A degraded control stays usable. The
 * control's own `disabled` prop (busy/validation) is passed through and still
 * takes effect for genuinely transient states.
 */
export type CapabilityControlProps = {
  "aria-disabled": boolean;
  "data-capability": string;
  "data-capability-status": CapabilityAvailability;
  /** Visible status text to render beside the control, or null when fine. */
  statusText: string | null;
  /** Whether the control should refuse activation (unavailable/unknown). */
  blocked: boolean;
};

export function capabilityControlProps(
  state: CapabilityState,
): CapabilityControlProps {
  const blocked = !state.enabled;
  return {
    "aria-disabled": blocked,
    "data-capability": state.id,
    "data-capability-status": state.availability,
    statusText: capabilityStatusText(state),
    blocked,
  };
}

// ---------------------------------------------------------------------------
// Fetch + cache (by registry_version)
// ---------------------------------------------------------------------------

type CacheEntry = {
  catalog: CapabilityCatalog;
  fetchedAt: number;
};

// The capability catalogue is public and identical for every viewer of a
// deployment, so a single module-level cache is correct. It is keyed by the
// registry version the last successful fetch reported; a changed version busts
// it (plan Task 9B: "Include the registry version, and refresh after a
// change"). The neutral unknown result is never cached, so the next attempt
// retries the API.
let cache: CacheEntry | null = null;

/** The minimum age before a background refresh re-checks the registry version. */
export const CAPABILITY_REFRESH_INTERVAL_MS = 60_000;

/** The current cached catalogue, or null when nothing valid is cached. */
export function cachedCatalog(): CapabilityCatalog | null {
  return cache?.catalog ?? null;
}

/**
 * Fetch the capability catalogue through the existing API client. On any
 * failure it returns the neutral unknown catalogue (never throws), so the
 * caller shows a 'status unknown' state for optional capabilities instead of
 * crashing. A successful fetch is cached by registry version; a cached
 * catalogue is reused while its version is unchanged unless `force` is set.
 */
export async function fetchCapabilities({
  workspaceId,
  signal,
  force = false,
}: {
  workspaceId: string;
  signal?: AbortSignal;
  force?: boolean;
}): Promise<CapabilityCatalog> {
  const now = Date.now();
  if (
    !force &&
    cache &&
    now - cache.fetchedAt < CAPABILITY_REFRESH_INTERVAL_MS
  ) {
    return cache.catalog;
  }
  try {
    const response = await editorApiRequest<CapabilitiesResponse>(
      `${env.apiBaseUrl}/capabilities`,
      {
        workspaceId,
        method: "GET",
        friendlyError: "Capability status is unavailable right now.",
        signal,
      },
    );
    const catalog = catalogFromResponse(response);
    // Bust the cache when the registry version changes; otherwise keep the
    // same object identity so consumers do not re-render needlessly.
    if (cache && cache.catalog.registryVersion === catalog.registryVersion) {
      cache = { catalog: cache.catalog, fetchedAt: now };
      return cache.catalog;
    }
    cache = { catalog, fetchedAt: now };
    return catalog;
  } catch {
    // Unreachable API: neutral unknown state for optional capabilities. Do not
    // cache it, so the next attempt retries. Keep a previously good catalogue
    // if one exists rather than regressing a known-good UI to unknown.
    return cache?.catalog ?? unknownCatalog();
  }
}
