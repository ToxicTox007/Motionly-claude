"use client";

import { Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";

type EditorialMemoryIndicator = {
  enabled: boolean;
  personalized: boolean;
  example_count: number;
  memory_version?: string | null;
  policy_revision: number;
  opt_out_available: boolean;
};

/**
 * Task 29: shows "Personalized from N edits" when a promoted, consented
 * editorial memory is influencing new plans for this project, with an opt-out
 * link to the account settings page where consent can be withdrawn. Withdrawing
 * consent removes the effect on new plans, so the indicator disappears.
 */
export function EditorialMemoryIndicator({
  workspaceId,
  projectId,
}: {
  workspaceId: string;
  projectId: string;
}) {
  const [indicator, setIndicator] = useState<EditorialMemoryIndicator | null>(
    null,
  );

  const requestOptions = useMemo(
    () => ({
      workspaceId,
      friendlyError: "Personalization status could not be loaded.",
    }),
    [workspaceId],
  );

  const load = useCallback(async () => {
    try {
      const next = await editorApiRequest<EditorialMemoryIndicator>(
        `${env.apiBaseUrl}/editorial-learning/projects/${encodeURIComponent(projectId)}/memory`,
        requestOptions,
      );
      setIndicator(next);
    } catch {
      setIndicator(null);
    }
  }, [projectId, requestOptions]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  if (!indicator?.enabled || !indicator.personalized) {
    return null;
  }

  const count = indicator.example_count;
  return (
    <div
      aria-live="polite"
      className="flex flex-wrap items-center gap-2 rounded-neu-md bg-surface px-3 py-2 text-xs shadow-neu-raised-sm ring-1 ring-primary/20"
      data-testid="editorial-memory-indicator"
    >
      <Sparkles className="size-4 text-primary" aria-hidden="true" />
      <span className="font-semibold text-primary">
        Personalized from {count} {count === 1 ? "edit" : "edits"}
      </span>
      {indicator.memory_version ? (
        <code className="font-mono text-xs text-muted-foreground">
          {indicator.memory_version.slice(0, 8)}
        </code>
      ) : null}
      {indicator.opt_out_available ? (
        <a
          className="ml-auto font-semibold text-muted-foreground underline underline-offset-2 hover:text-foreground"
          href="/account"
        >
          Opt out
        </a>
      ) : null}
    </div>
  );
}
