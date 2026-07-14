"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
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
  const contentRef = useRef<HTMLDivElement>(null);

  // Focus the dialog on mount
  useEffect(() => {
    contentRef.current?.focus();
  }, []);

  const handleReload = useCallback(async () => {
    setLoading("reload");
    try {
      await onReload();
    } finally {
      setLoading(null);
    }
  }, [onReload]);

  const handleForceSave = useCallback(async () => {
    setLoading("force");
    try {
      await onForceSave();
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
      contentRef={contentRef}
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
        <div className="flex items-start gap-3 rounded border-2 border-warning bg-warning/10 p-3">
          <AlertTriangle
            className="mt-0.5 size-5 shrink-0 text-warning"
            aria-hidden="true"
          />
          <div className="text-sm font-bold text-foreground">
            <p>
              Someone else (or another tab) modified this plan while you were
              editing. Your version is at revision{" "}
              <span className="font-black">{conflict.expectedRevision}</span>,
              but the server is at revision{" "}
              <span className="font-black">{conflict.currentRevision}</span>.
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
            <strong className="font-black">Reload &amp; Discard</strong> — fetch
            the latest version from the server. Your unsaved changes will be
            lost.
          </p>
          <p>
            <strong className="font-black">Force Save</strong> — overwrite the
            server version with your local changes. The other person&apos;s edits
            will be lost.
          </p>
        </div>
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
