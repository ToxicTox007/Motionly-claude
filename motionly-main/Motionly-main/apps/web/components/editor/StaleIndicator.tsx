"use client";

import React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/button";
import { cn } from "@/lib/utils";

interface StaleIndicatorProps {
  /** Descriptive message explaining what is stale */
  message?: string;
  /** Label for the action button */
  actionLabel?: string;
  /** Callback when the user clicks the action button */
  onAction?: () => void;
  /** Additional CSS class */
  className?: string;
}

/**
 * Small banner/badge indicating that a derivative (render, export) is stale
 * because the timeline has been modified since the last render.
 */
export function StaleIndicator({
  message = "Render is outdated — timeline has been modified since last render",
  actionLabel = "Re-render",
  onAction,
  className,
}: StaleIndicatorProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex items-center gap-2 rounded border-2 border-warning bg-warning/10 px-3 py-2",
        className,
      )}
    >
      <AlertTriangle
        className="size-4 shrink-0 text-warning"
        aria-hidden="true"
      />
      <span className="flex-1 text-xs font-bold text-foreground">
        {message}
      </span>
      {onAction && (
        <Button
          variant="secondary"
          size="sm"
          onClick={onAction}
          aria-label={actionLabel}
          className="shrink-0"
        >
          <RefreshCw className="size-3.5" aria-hidden="true" />
          {actionLabel}
        </Button>
      )}
    </div>
  );
}
