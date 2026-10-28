"use client";

import { useEffect, useRef } from "react";
import type { TranscriptStatus, TranscriptSummary } from "@motionly/shared";
import { apiFetch, startAdaptivePolling } from "@/lib/client-api";
import { env } from "@/lib/env";

const SETTLED_STATUSES = new Set<TranscriptStatus>([
  "ready",
  "failed",
  "captions_unavailable",
]);

/** A transcript that will not change without a new request from the user. */
export function isTranscriptSettled(status: TranscriptStatus | null) {
  return status !== null && SETTLED_STATUSES.has(status);
}

/**
 * Polls the project's transcript until it settles (ready, failed or captions
 * unavailable). The poller is keyed on the upload and on whether the
 * transcript has settled, not on the upload object or each interim status,
 * so a router refresh or a queued → processing change does not restart it
 * with an immediate extra request, and a failed transcript is not re-polled.
 */
export function useTranscriptPolling({
  projectId,
  workspaceId,
  uploadId,
  uploadReady,
  transcriptStatus,
  onTranscript,
}: {
  projectId: string;
  workspaceId: string;
  uploadId: string | null;
  uploadReady: boolean;
  transcriptStatus: TranscriptStatus | null;
  onTranscript: (transcript: TranscriptSummary | null) => void;
}) {
  const settled = isTranscriptSettled(transcriptStatus);
  const onTranscriptRef = useRef(onTranscript);
  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  useEffect(() => {
    if (!uploadId || !uploadReady || settled) return;

    let cancelled = false;
    let stopPolling: () => void = () => undefined;
    stopPolling = startAdaptivePolling(
      async (signal) => {
        try {
          const response = await apiFetch(
            `${env.apiBaseUrl}/projects/${projectId}/transcript`,
            { cache: "no-store", signal },
            { workspaceId },
          );
          if (cancelled) return;
          if (response.status === 404) {
            onTranscriptRef.current(null);
            return;
          }
          if (!response.ok) return;
          const next = (await response.json()) as TranscriptSummary;
          if (cancelled) return;
          onTranscriptRef.current(next);
          if (isTranscriptSettled(next.status)) stopPolling();
        } catch (error) {
          if (!signal.aborted) console.error(error);
        }
      },
      { intervalMs: 5000, maxIntervalMs: 15000 },
    );
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [projectId, settled, uploadId, uploadReady, workspaceId]);
}
