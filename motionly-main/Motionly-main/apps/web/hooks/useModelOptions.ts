"use client";

// Task 32 — React hook over the model-options catalogue (plan Section 2.11).
//
// `useModelOptions` loads the authenticated `GET /model-options` catalogue for
// the workspace (or a project, so the effective choice reflects the project
// override) and exposes a small, never-throwing surface the pickers render. On
// a failed fetch it surfaces the neutral `statusUnknown` catalogue, exactly as
// `useCapabilities` does for the public capability catalogue.

import { useCallback, useEffect, useMemo, useState } from "react";
import { startAdaptivePolling } from "@/lib/client-api";
import {
  type CapabilityOptions,
  type ModelOptionsCatalog,
  capabilityOptions,
  fetchModelOptions,
  unknownOptionsCatalog,
} from "@/lib/model-options";

export type UseModelOptionsResult = {
  /** The whole options catalogue (per capability). */
  catalog: ModelOptionsCatalog;
  /** Resolve one capability's options block, or null when absent. */
  resolve: (capability: string) => CapabilityOptions | null;
  /** True until the first fetch settles. */
  loading: boolean;
  /** True when the catalogue is the neutral unreachable-API state. */
  statusUnknown: boolean;
  /** Force a re-fetch (e.g. after a setting changes the effective choice). */
  refresh: () => Promise<void>;
};

export function useModelOptions(
  workspaceId: string,
  projectId?: string,
): UseModelOptionsResult {
  const [catalog, setCatalog] = useState<ModelOptionsCatalog>(() =>
    unknownOptionsCatalog(),
  );
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    async (signal: AbortSignal | undefined) => {
      const next = await fetchModelOptions({ workspaceId, projectId, signal });
      if (signal?.aborted) return;
      setCatalog(next);
      setLoading(false);
    },
    [workspaceId, projectId],
  );

  useEffect(() => {
    // Mirror the editor's blessed polling pattern (see hooks/useCapabilities
    // and lib/client-api startAdaptivePolling): a visibility/offline-aware
    // load that re-checks the effective choice so a setting change elsewhere is
    // reflected without a reload. Deferring through the poller keeps the first
    // load off the synchronous effect path.
    const stop = startAdaptivePolling(
      async (signal) => {
        await load(signal).catch(() => undefined);
      },
      {
        intervalMs: 60_000,
        maxIntervalMs: 60_000,
        backoffStepMs: 0,
        immediate: true,
      },
    );
    return stop;
  }, [load]);

  const refresh = useCallback(async () => {
    await load(undefined);
  }, [load]);

  const resolve = useCallback(
    (capability: string) => capabilityOptions(catalog, capability),
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
