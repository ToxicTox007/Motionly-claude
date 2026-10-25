"use client";

// Task 32 — the fallback notice shown where AI results surface a
// `model_choice` with a `fallback_reason` (plan Section 2.11: "the UI tells the
// user when a fallback happened"). It is a small presentational helper, like
// `CapabilityStatus`: an icon plus text carry the meaning, so the state is
// never signalled by colour alone. It never shows a model id, a route id, a URL
// or a secret — only neutral, operator-safe copy.
//
// The `model_choice` prop is typed locally (see `lib/model-options.ts`) so this
// component does not depend on a generated type that may not exist yet in this
// working tree while Task 31 lands the field on AI-result responses.

import { Info } from "lucide-react";
import { type ModelChoice, fallbackNoticeText } from "@/lib/model-options";

export function ModelChoiceNotice({
  modelChoice,
  id,
}: {
  modelChoice: ModelChoice | null | undefined;
  id?: string;
}) {
  const text = fallbackNoticeText(modelChoice);
  if (!text) return null;
  return (
    <p
      id={id}
      role="status"
      data-testid="model-choice-fallback"
      data-model-choice-fallback="true"
      className="mt-2 flex items-start gap-1.5 rounded-neu-md bg-surface px-3 py-2 text-xs font-medium text-muted-foreground shadow-neu-inset-sm"
    >
      <Info
        className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
        strokeWidth={1.75}
        aria-hidden="true"
      />
      <span>{text}</span>
    </p>
  );
}
