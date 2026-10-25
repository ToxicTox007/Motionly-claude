"use client";

// Task 9B — a small presentational helper every gated control uses to render
// the visible status text beneath it. State is never signalled by colour
// alone: an icon plus text carry the meaning, and the text is the exact
// operator-facing copy from the plan. The model-picker task (Section 2.11)
// reuses this beside a disabled plan-locked option.

import { AlertCircle, Info } from "lucide-react";
import { type CapabilityState, capabilityStatusText } from "@/lib/capabilities";

/**
 * Render the capability status line, or nothing when the capability is fully
 * available. `id` ties the message to the control via aria-describedby so a
 * screen reader announces why the control is disabled.
 */
export function CapabilityStatus({
  state,
  id,
}: {
  state: CapabilityState;
  id?: string;
}) {
  const text = capabilityStatusText(state);
  if (!text) return null;
  const degraded = state.availability === "degraded";
  const Icon = degraded ? Info : AlertCircle;
  return (
    <p
      id={id}
      role="status"
      data-capability={state.id}
      data-capability-status={state.availability}
      className="mt-2 flex items-start gap-1.5 rounded-neu-md bg-surface px-3 py-2 text-xs font-medium text-muted-foreground shadow-neu-inset-sm"
    >
      <Icon
        className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
        strokeWidth={1.75}
        aria-hidden="true"
      />
      <span>{text}</span>
    </p>
  );
}
