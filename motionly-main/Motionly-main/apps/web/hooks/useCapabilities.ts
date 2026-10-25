"use client";

// Task 9B — React hook over the capability catalogue.
//
// `useCapabilities` loads the catalogue once per editor session and refreshes
// it on a light interval so a registry change (a newly configured or removed
// route) is picked up without a reload. It never throws: a failed fetch
// surfaces as the neutral `statusUnknown` catalogue, and `resolve` always
// returns a renderable state.
//
// The model-picker task (Section 2.11) reuses this hook unchanged: it exposes
// the same `catalog`/`resolve` surface the gating controls use, so a picker can
// read a capability's status the same way a gated button does.

import { useCallback, useEffect, useMemo, useState } from "react";
import { startAdaptivePolling } from "@/lib/client-api";
import {
  CAPABILITY_REFRESH_INTERVAL_MS,
  type CapabilityCatalog,
  type CapabilityState,
  cachedCatalog,
  fetchCapabilities,
  resolveCapability,
  unknownCatalog,
} from "@/lib/capabilities";

export type UseCapabilitiesResult = {
  /** The whole capability catalogue (keyed by id) with its registry version. */
  catalog: CapabilityCatalog;
  /** Resolve one capability id to a renderable state (never throws). */
  resolve: (id: string) => CapabilityState;
  /** True until the first fetch settles. */
  loading: boolean;
  /** True when the catalogue is the neutral unreachable-API state. */
  statusUnknown: boolean;
  /** Force a re-fetch (e.g. after the user reconnects). */
  refresh: () => Promise<void>;
};

export function useCapabilities(workspaceId: string): UseCapabilitiesResult {
  const [catalog, setCatalog] = useState<CapabilityCatalog>(
    () => cachedCatalog() ?? unknownCatalog(),
  );
  const [loading, setLoading] = useState(() => cachedCatalog() === null);

  const load = useCallback(
    async (signal: AbortSignal | undefined, force: boolean) => {
      const next = await fetchCapabilities({ workspaceId, signal, force });
      if (signal?.aborted) return;
      // Only re-render when the registry version actually changed, so a stable
      // catalogue does not churn consumers. A move to/from the neutral unknown
      // state always updates.
      setCatalog((current) => {
        const changed =
          current.registryVersion !== next.registryVersion ||
          current.statusUnknown !== next.statusUnknown;
        return changed ? next : current;
      });
      // The first settle clears the loading flag. This runs after an await, so
      // it is not a synchronous set-state in the effect body.
      setLoading(false);
    },
    [workspaceId],
  );

  useEffect(() => {
    // Mirror the editor's blessed polling pattern (see hooks/useWorkflowStatus
    // and lib/client-api startAdaptivePolling): an immediate first load, then a
    // visibility/offline-aware interval that re-checks the registry version so
    // a newly configured or removed route is picked up without a reload. The
    // interval forces past the short in-module cache window.
    const stop = startAdaptivePolling(
      async (signal) => {
        await load(signal, true).catch(() => undefined);
      },
      {
        intervalMs: CAPABILITY_REFRESH_INTERVAL_MS,
        maxIntervalMs: CAPABILITY_REFRESH_INTERVAL_MS,
        backoffStepMs: 0,
        immediate: true,
      },
    );
    return stop;
  }, [load]);

  const refresh = useCallback(async () => {
    await load(undefined, true);
  }, [load]);

  const resolve = useCallback(
    (id: string) => resolveCapability(catalog, id),
    [catalog],
  );

  return useMemo(
    () => ({
      catalog,
      resolve,
      loading,
      statusUnknown: catalog.statusUnknown,
      refresh,
    }),
    [catalog, resolve, loading, refresh],
  );
}
