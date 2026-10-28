"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RevisionConflictError } from "@/lib/client-api";

/**
 * A field-level description of a change for the "what will be overwritten"
 * summary shown in the conflict dialog. Keeps the diff accessible and small.
 */
export interface ConflictChangeSummary {
  field: string;
  detail: string;
}

export interface ConflictState {
  expectedRevision: number;
  currentRevision: number;
  conflictedAt: number;
  source: string; // which mutation caused it
  /** Human-readable summary of the local change that was rejected. */
  localSummary: ConflictChangeSummary[];
}

/**
 * Extra handlers a mutation call site can register when a conflict is detected.
 * They let "Force Save" actually re-send the SAME change against the latest
 * revision, and let the canvas roll back an optimistic on-screen change.
 */
export interface ConflictHandlers {
  /**
   * Re-apply the exact local change against a freshly fetched revision. The
   * caller receives the latest plan revision and MUST resend with it as the
   * expected revision. Returns when the save has succeeded.
   */
  retry?: (latestRevision: number) => Promise<void>;
  /** Revert an optimistic on-screen change (drag/resize) after a conflict. */
  rollback?: () => void;
  /** Summary of the local change, for the overwrite preview. */
  localSummary?: ConflictChangeSummary[];
}

/**
 * Tracks the current plan/media revision for optimistic concurrency control.
 * Backward compatible — handles null revisions from older API responses.
 *
 * The plan revision belongs to one plan of one project. It starts from
 * `seedRevision` (the active plan's revision as loaded), so the very first
 * edit already sends `expected_revision`; without it the API applies the
 * write unconditionally and a concurrent edit would be silently overwritten.
 * When the project or plan changes, tracking restarts from the new plan's
 * seed rather than carrying the old plan's revision over.
 *
 * @param fetchLatestRevision resolves the authoritative current plan revision
 *   from the server; used by force-save to retry against the latest revision.
 */
export function useRevisionTracking(
  projectId: string,
  planId: string | null,
  fetchLatestRevision?: () => Promise<number | null>,
  seedRevision: number | null = null,
) {
  const scope = `${projectId}:${planId ?? ""}`;
  const [trackedScope, setTrackedScope] = useState(scope);
  const [planRevision, setPlanRevision] = useState<number | null>(seedRevision);
  const [mediaRevision, setMediaRevision] = useState<number | null>(null);
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null);
  const [conflictState, setConflictState] = useState<ConflictState | null>(
    null,
  );

  // Keep a ref for the current revision so async callbacks always read latest
  const planRevisionRef = useRef<number | null>(seedRevision);
  // The handlers registered for the active conflict so Force Save / Discard can
  // re-apply or roll back the exact local change.
  const conflictHandlersRef = useRef<ConflictHandlers | null>(null);

  // A different plan (or project) is now active: re-seed from its revision.
  if (trackedScope !== scope) {
    setTrackedScope(scope);
    setPlanRevision(seedRevision);
  }
  useEffect(() => {
    planRevisionRef.current = planRevision;
  }, [planRevision]);

  const updateRevision = useCallback(
    (
      newRevision: number | null | undefined,
      kind: "plan" | "media" = "plan",
    ) => {
      if (newRevision == null) return; // Old API without revision support
      if (kind === "plan") {
        setPlanRevision(newRevision);
        planRevisionRef.current = newRevision;
      } else {
        setMediaRevision(newRevision);
      }
      setLastSyncedAt(Date.now());
    },
    [],
  );

  const handleConflict = useCallback(
    (error: unknown, source: string, handlers?: ConflictHandlers): boolean => {
      if (error instanceof RevisionConflictError) {
        conflictHandlersRef.current = handlers ?? null;
        setConflictState({
          expectedRevision: error.expectedRevision,
          currentRevision: error.currentRevision,
          conflictedAt: Date.now(),
          source,
          localSummary: handlers?.localSummary ?? [],
        });
        return true;
      }
      return false;
    },
    [],
  );

  const clearConflict = useCallback(() => {
    conflictHandlersRef.current = null;
    setConflictState(null);
  }, []);

  /**
   * Discard the local change: roll back any optimistic on-screen edit, then
   * clear the conflict. The caller still reloads the authoritative plan.
   */
  const discardConflict = useCallback(() => {
    conflictHandlersRef.current?.rollback?.();
    clearConflict();
  }, [clearConflict]);

  /**
   * Force save: fetch the latest revision, re-send the SAME local change
   * against it, then clear the conflict. NOT a no-op and NOT a stale write —
   * if no retry handler was registered we cannot safely force save and leave
   * the dialog open.
   */
  const forceSave = useCallback(async () => {
    const handlers = conflictHandlersRef.current;
    if (!handlers?.retry) {
      throw new Error(
        "This change cannot be force-saved automatically. Reload and re-apply it.",
      );
    }
    const latest =
      (await fetchLatestRevision?.()) ?? conflictState?.currentRevision ?? null;
    if (latest == null) {
      throw new Error(
        "Could not determine the latest revision to save against.",
      );
    }
    // Keep our tracked revision in sync before retrying so any downstream
    // reads (and the resend itself) target the latest revision.
    planRevisionRef.current = latest;
    setPlanRevision(latest);
    await handlers.retry(latest);
    clearConflict();
  }, [clearConflict, conflictState, fetchLatestRevision]);

  const getExpectedRevision = useCallback((): number | undefined => {
    return planRevisionRef.current ?? undefined;
  }, []);

  return {
    planRevision,
    mediaRevision,
    lastSyncedAt,
    conflictState,
    updateRevision,
    handleConflict,
    clearConflict,
    discardConflict,
    forceSave,
    getExpectedRevision,
  };
}
