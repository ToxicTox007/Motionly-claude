"use client";

import React, { useCallback, useState } from "react";
import { AlertTriangle, Loader2, RotateCcw, Upload } from "lucide-react";
import { Button } from "@/components/button";
import { Modal } from "@/components/brutal-ui";
import type { ConflictState } from "@/hooks/useRevisionTracking";

interface ConflictDialogProps {
  conflict: ConflictState;
  onReload: () => Promise<void>;
  onForceSave: () => Promise<void>;
  onDismiss: () => void;
}

/**
 * Modal dialog shown when a revision conflict (HTTP 409) is detected.
 * Offers two resolution paths:
 * - Reload & Discard: fetches latest plan from server, discards local changes
 * - Force Save: retries the mutation without expected_revision (overwrites)
 */
export function ConflictDialog({
  conflict,
  onReload,
  onForceSave,
  onDismiss,
}: ConflictDialogProps) {
  const [loading, setLoading] = useState<"reload" | "force" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleReload = useCallback(async () => {
    setLoading("reload");
    setActionError(null);
    try {
      await onReload();
    } catch (error) {
      // A failed reload keeps the dialog open and says so, instead of a
      // spinner that stops with nothing explained.
      setActionError(
        error instanceof Error && error.message
          ? `${error.message} Check your connection and try again.`
          : "The latest version could not be loaded. Check your connection and try again.",
      );
    } finally {
      setLoading(null);
    }
  }, [onReload]);

  const handleForceSave = useCallback(async () => {
    setLoading("force");
    setActionError(null);
    try {
      await onForceSave();
    } catch (error) {
      setActionError(
        error instanceof Error
          ? error.message
          : "Force save failed. Please reload and try again.",
      );
    } finally {
      setLoading(null);
    }
  }, [onForceSave]);

  const timeSinceConflict = formatTimeSince(conflict.conflictedAt);

  return (
    <Modal
      open
      title="Version Conflict"
      description="Your changes conflict with a newer version of this plan."
      onClose={onDismiss}
      // While Reload or Force Save runs, the backdrop, Escape and Close do
      // nothing: the result must land on an open dialog.
      closeDisabled={loading !== null}
      footer={
        <>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleReload}
            disabled={loading !== null}
            aria-label="Reload plan from server and discard local changes"
          >
            {loading === "reload" ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <RotateCcw className="size-4" aria-hidden="true" />
            )}
            Reload &amp; Discard
          </Button>
          <Button
            variant="danger"
            size="sm"
            onClick={handleForceSave}
            disabled={loading !== null}
            aria-label="Force save your changes, overwriting the newer version"
          >
            {loading === "force" ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Upload className="size-4" aria-hidden="true" />
            )}
            Force Save
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="flex items-start gap-3 rounded-neu-md bg-surface p-3 shadow-neu-raised-sm ring-1 ring-warning/30">
          <AlertTriangle
            className="mt-0.5 size-5 shrink-0 text-warning"
            aria-hidden="true"
          />
          <div className="text-sm font-semibold text-foreground">
            <p>
              Someone else (or another tab) modified this plan while you were
              editing. Your version is at revision{" "}
              <span className="font-bold tabular-nums">
                {conflict.expectedRevision}
              </span>
              , but the server is at revision{" "}
              <span className="font-bold tabular-nums">
                {conflict.currentRevision}
              </span>
              .
            </p>
            {timeSinceConflict && (
              <p className="mt-1 text-xs text-muted-foreground">
                Detected {timeSinceConflict}
              </p>
            )}
          </div>
        </div>

        <div className="space-y-2 text-sm text-muted-foreground">
          <p>
            <strong className="font-semibold text-foreground">
              Reload &amp; Discard
            </strong>{" "}
            — fetch the latest version from the server. Your unsaved changes
            will be lost.
          </p>
          <p>
            <strong className="font-semibold text-foreground">
              Force Save
            </strong>{" "}
            — re-apply your change on top of the latest version. The change
            below will overwrite the newer server copy.
          </p>
        </div>

        {conflict.localSummary && conflict.localSummary.length > 0 && (
          <div
            className="space-y-1 rounded-neu-md bg-muted p-3 shadow-neu-inset-sm"
            aria-label="Changes that Force Save will overwrite on the server"
          >
            <p className="text-xs font-semibold text-foreground">
              Will overwrite the server with your change
            </p>
            <ul className="list-disc space-y-0.5 pl-5 text-sm text-foreground">
              {conflict.localSummary.map((change, index) => (
                <li key={`${change.field}-${index}`}>
                  <span className="font-semibold">{change.field}:</span>{" "}
                  {change.detail}
                </li>
              ))}
            </ul>
          </div>
        )}

        {actionError && (
          <p
            className="rounded-neu-md bg-surface p-3 text-sm font-semibold text-danger shadow-neu-raised-sm ring-1 ring-danger/30"
            role="alert"
          >
            {actionError}
          </p>
        )}
      </div>
    </Modal>
  );
}

function formatTimeSince(timestamp: number): string | null {
  const seconds = Math.floor((Date.now() - timestamp) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return null;
}
