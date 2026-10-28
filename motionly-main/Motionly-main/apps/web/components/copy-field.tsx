"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/button";
import { copyText, selectElementText } from "@/lib/clipboard";

const COPIED_RESET_MS = 2000;

/**
 * A value (API key, invite or share link) with a Copy button.
 *
 * - The value is never inside a live region, so a screen reader does not read
 *   a secret aloud; a separate hidden status says "copied" or "copy failed".
 * - If the browser refuses the copy, the text is selected and the user is told
 *   to press Ctrl+C / ⌘C, instead of a silent failure or a false "Copied".
 */
export function CopyField({
  value,
  label,
  testId,
  className,
}: {
  value: string;
  /** What is being copied, e.g. "API key"; describes the button. */
  label: string;
  testId?: string;
  className?: string;
}) {
  const valueRef = useRef<HTMLElement>(null);
  const descriptionId = useId();
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    if (state !== "copied") return;
    const timer = window.setTimeout(() => setState("idle"), COPIED_RESET_MS);
    return () => window.clearTimeout(timer);
  }, [state]);

  const copy = async () => {
    const result = await copyText(value);
    if (result.ok) {
      setState("copied");
      setAnnouncement(`${label} copied.`);
      return;
    }
    selectElementText(valueRef.current);
    setState("failed");
    setAnnouncement(
      `Copy failed. The ${label.toLowerCase()} is selected; press Ctrl+C or Command+C to copy it.`,
    );
  };

  return (
    <div className={className}>
      <div className="flex items-center gap-2">
        <code
          ref={valueRef}
          className="flex-1 overflow-x-auto rounded-neu bg-surface px-3 py-2 font-mono text-xs text-foreground shadow-neu-inset"
          data-testid={testId}
        >
          {value}
        </code>
        <Button
          type="button"
          size="sm"
          onClick={() => void copy()}
          aria-describedby={descriptionId}
        >
          {state === "copied" ? (
            <Check className="size-4" aria-hidden="true" />
          ) : (
            <Copy className="size-4" aria-hidden="true" />
          )}
          {state === "copied" ? "Copied" : "Copy"}
        </Button>
        <span id={descriptionId} hidden>
          {label}
        </span>
      </div>
      {state === "failed" ? (
        <p className="mt-2 text-xs font-semibold text-muted-foreground">
          Press Ctrl+C / ⌘C to copy.
        </p>
      ) : null}
      <span className="sr-only" role="status" aria-live="polite">
        {announcement}
      </span>
    </div>
  );
}
