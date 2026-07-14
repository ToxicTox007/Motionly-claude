"use client";

import { useCallback, useRef, useState } from "react";
import { RevisionConflictError } from "@/lib/client-api";

export interface RevisionState {
  planRevision: number | null;
  mediaRevision: number | null;
  lastSyncedAt: number | null;
  conflictState: ConflictState | null;
}

export interface ConflictState {
  expectedRevision: number;
  currentRevision: number;
  conflictedAt: number;
  source: string; // which mutation caused it
}

/**
 * Tracks the current plan/media revision for optimistic concurrency control.
 * Backward compatible — handles null revisions from older API responses.
 */
export function useRevisionTracking(projectId: string, planId: string | null) {
  const [planRevision, setPlanRevision] = useState<number | null>(null);
  const [mediaRevision, setMediaRevision] = useState<number | null>(null);
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null);
  const [conflictState, setConflictState] = useState<ConflictState | null>(null);

  // Keep a ref for the current revision so async callbacks always read latest
  const planRevisionRef = useRef<number | null>(null);

  const updateRevision = useCallback(
    (newRevision: number | null | undefined, kind: "plan" | "media" = "plan") => {
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
    (error: unknown, source: string): boolean => {
      if (error instanceof RevisionConflictError) {
        setConflictState({
          expectedRevision: error.expectedRevision,
          currentRevision: error.currentRevision,
          conflictedAt: Date.now(),
          source,
        });
        return true;
      }
      return false;
    },
    [],
  );

  const clearConflict = useCallback(() => {
    setConflictState(null);
  }, []);

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
    getExpectedRevision,
  };
}
