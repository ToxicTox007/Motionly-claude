"use client";

import { useRouter } from "next/navigation";
import { useId, useTransition } from "react";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/button";
import { PixelIcon } from "@/components/pixel-icons";
import {
  EDITOR_PANEL_LABELS,
  type EditorPanelErrors,
  type EditorPanelKey,
} from "@/lib/editor-panels";

function listLabels(panels: EditorPanelKey[]) {
  const labels = panels.map((panel) => EDITOR_PANEL_LABELS[panel]);
  if (labels.length <= 1) return labels.join("");
  return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}

/**
 * Shown in place of a panel whose data failed to load, so a failure is never
 * mistaken for an empty project. Retry re-fetches the editor's server data;
 * once the panel loads, the editor remounts with it (see panelErrorKey).
 */
export function PanelLoadError({
  panels,
  errors,
  compact = false,
}: {
  panels: EditorPanelKey[];
  errors: EditorPanelErrors;
  /** A notice above content that still works, rather than a replacement. */
  compact?: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const headingId = useId();
  if (panels.length === 0) return null;
  const references = panels
    .map((panel) => errors[panel]?.requestId)
    .filter((value): value is string => Boolean(value));

  return (
    <section
      aria-labelledby={headingId}
      aria-busy={pending || undefined}
      className="mb-4 flex gap-3 rounded-neu-md bg-surface p-4 text-sm shadow-neu-raised"
      data-testid="panel-load-error"
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded-neu-sm text-danger shadow-neu-inset-sm">
        <PixelIcon name="warning" className="size-5" />
      </span>
      <div className="min-w-0">
        <h3 id={headingId} className="font-semibold text-foreground">
          Couldn&apos;t load {listLabels(panels)}
        </h3>
        <p className="mt-1 font-medium text-muted-foreground">
          {compact
            ? "The rest of the editor works. Nothing you saved has changed."
            : "Nothing you saved has changed. Try again in a moment."}
        </p>
        {references.length ? (
          <p className="mt-1 text-xs font-medium text-muted-foreground">
            Reference:{" "}
            <span className="font-mono">{references.join(", ")}</span>
          </p>
        ) : null}
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="mt-3"
          disabled={pending}
          aria-busy={pending}
          onClick={() => startTransition(() => router.refresh())}
        >
          <RotateCcw className="size-4" aria-hidden="true" />
          {pending ? "Retrying…" : "Retry"}
        </Button>
      </div>
    </section>
  );
}
