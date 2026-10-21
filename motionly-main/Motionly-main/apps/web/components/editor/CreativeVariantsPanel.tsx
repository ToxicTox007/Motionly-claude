"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Loader2, Sparkles } from "lucide-react";
import type { EditPlanRecordSummary } from "@motionly/shared";
import { Button } from "@/components/button";
import { editorApiRequest, RevisionConflictError } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

type CreativeVariantDimension =
  | "hook"
  | "caption_style"
  | "broll"
  | "pacing"
  | "opening_frame";

type CreativeVariant = {
  id: string;
  ordinal: number;
  label: string;
  primary_dimension: CreativeVariantDimension;
  hook: { label: string; text: string };
  caption: { preset: string; label: string; rationale: string };
  broll: { density: string; direction: string; shot_strategy: string };
  pacing: {
    label: string;
    cut_duration_multiplier: number;
    direction: string;
  };
  opening_frame: {
    label: string;
    start_ms: number;
    frame_direction: string;
    thumbnail_direction: string;
  };
  comparison_notes: string[];
};

type CreativeVariantSet = {
  id: string;
  name: string;
  revision: number;
  source_plan_id: string;
  source_plan_revision: number;
  dimensions: CreativeVariantDimension[];
  variants: CreativeVariant[];
  selected_variant_id: string | null;
  review_status: "needs_review" | "selected" | "applied";
  review_only: boolean;
  applied_variant_id: string | null;
  applied_plan_id: string | null;
  applied_plan_revision: number | null;
  created_at: string;
};

type CreativeVariantSetList = { variant_sets: CreativeVariantSet[] };

type CreativeVariantApplyResult = {
  variant_set: CreativeVariantSet;
  plan: EditPlanRecordSummary;
  plan_revision: number;
  applied_dimension: CreativeVariantDimension;
  render_enqueued: boolean;
  render_job_id: string | null;
};

interface CreativeVariantsPanelProps {
  projectId: string;
  workspaceId: string;
  activePlan: EditPlanRecordSummary | null;
  onPlanUpdated?: (plan: EditPlanRecordSummary) => void;
}

const DIMENSIONS: Array<{ id: CreativeVariantDimension; label: string }> = [
  { id: "hook", label: "Hooks" },
  { id: "caption_style", label: "Caption style" },
  { id: "broll", label: "B-roll" },
  { id: "pacing", label: "Cut pacing" },
  { id: "opening_frame", label: "Opening frame" },
];

type CreativeVariantRequestOptions = Pick<
  CreativeVariantsPanelProps,
  "workspaceId"
>;

function requestOptions({ workspaceId }: CreativeVariantRequestOptions) {
  return { workspaceId };
}

export function CreativeVariantsPanel(props: CreativeVariantsPanelProps) {
  const { activePlan, projectId, workspaceId, onPlanUpdated } = props;
  const [dimensions, setDimensions] = useState<CreativeVariantDimension[]>(() =>
    DIMENSIONS.map((dimension) => dimension.id),
  );
  const [variantCount, setVariantCount] = useState(5);
  const [sets, setSets] = useState<CreativeVariantSet[]>([]);
  const [activeSetId, setActiveSetId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  const fetchSets = useCallback(
    () =>
      editorApiRequest<CreativeVariantSetList>(
        `${env.apiBaseUrl}/projects/${projectId}/creative-variants`,
        {
          ...requestOptions({ workspaceId }),
          friendlyError: "Creative test history could not be loaded.",
        },
      ),
    [projectId, workspaceId],
  );

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void fetchSets()
        .then((response) => {
          if (cancelled) return;
          setSets(response.variant_sets);
          setActiveSetId((current) =>
            current && response.variant_sets.some((set) => set.id === current)
              ? current
              : (response.variant_sets[0]?.id ?? null),
          );
        })
        .catch((error) => {
          if (!cancelled) setMessage((error as Error).message);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [fetchSets]);

  const activeSet = useMemo(
    () => sets.find((set) => set.id === activeSetId) ?? sets[0] ?? null,
    [activeSetId, sets],
  );

  function toggleDimension(dimension: CreativeVariantDimension) {
    if (dimensions.includes(dimension)) {
      if (dimensions.length === 1) return;
      const next = dimensions.filter((item) => item !== dimension);
      setDimensions(next);
      setVariantCount((count) => Math.max(1, Math.min(count, 6)));
      return;
    }
    const next = [...dimensions, dimension];
    setDimensions(next);
    setVariantCount((count) => Math.max(count, next.length));
  }

  async function generateSet() {
    if (!activePlan || !dimensions.length) return;
    setBusy(true);
    setMessage(null);
    try {
      const created = await editorApiRequest<CreativeVariantSet>(
        `${env.apiBaseUrl}/projects/${projectId}/creative-variants`,
        {
          ...requestOptions({ workspaceId }),
          method: "POST",
          body: {
            name: `Creative test · ${new Date().toLocaleDateString()}`,
            dimensions,
            variant_count: Math.max(variantCount, dimensions.length),
          },
          friendlyError: "Creative test set could not be generated.",
        },
      );
      setSets((current) => [created, ...current]);
      setActiveSetId(created.id);
      setMessage(
        "Review set ready. Compare directions, then mark one preferred.",
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function selectVariant(variantId: string) {
    if (!activeSet) return;
    setBusy(true);
    setMessage(null);
    try {
      const updated = await editorApiRequest<CreativeVariantSet>(
        `${env.apiBaseUrl}/projects/${projectId}/creative-variants/${activeSet.id}/selection`,
        {
          ...requestOptions({ workspaceId }),
          method: "PATCH",
          body: {
            variant_id: variantId,
            expected_revision: activeSet.revision,
          },
          friendlyError: "Preferred direction could not be saved.",
        },
      );
      setSets((current) =>
        current.map((set) => (set.id === updated.id ? updated : set)),
      );
      setMessage("Preferred direction saved for review. Nothing was rendered.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const fetchLatestPlanRevision = useCallback(async (): Promise<
    number | null
  > => {
    if (!activePlan) return null;
    const refreshed = await editorApiRequest<EditPlanRecordSummary>(
      `${env.apiBaseUrl}/projects/${projectId}/plans/${activePlan.id}`,
      {
        ...requestOptions({ workspaceId }),
        friendlyError: "Could not reload the current plan revision.",
      },
    );
    return refreshed.revision ?? null;
  }, [activePlan, projectId, workspaceId]);

  const applyVariant = useCallback(
    async (variantId: string, render: boolean) => {
      if (!activeSet || !activePlan) return;
      setBusy(true);
      setMessage(null);

      // The apply route creates a NEW plan version from the chosen variant, so
      // the active plan's revision guards the write. A stale revision returns
      // the standard 409; handle it the same way the editor does — refetch the
      // latest revision once and retry the same change (handleConflict/retry).
      const runApply = async (expectedPlanRevision: number) =>
        editorApiRequest<CreativeVariantApplyResult>(
          `${env.apiBaseUrl}/projects/${projectId}/creative-variants/${activeSet.id}/apply`,
          {
            ...requestOptions({ workspaceId }),
            method: "POST",
            body: {
              variant_id: variantId,
              expected_revision: activeSet.revision,
              expected_plan_revision: expectedPlanRevision,
              render,
            },
            friendlyError: "The variant could not be applied to the edit.",
          },
        );

      try {
        let result: CreativeVariantApplyResult;
        try {
          result = await runApply(activePlan.revision ?? 1);
        } catch (error) {
          if (!(error instanceof RevisionConflictError)) throw error;
          // Conflict: reload the latest revision and retry the same apply.
          const latest =
            (await fetchLatestPlanRevision()) ?? error.currentRevision;
          result = await runApply(latest);
        }

        setSets((current) =>
          current.map((set) =>
            set.id === result.variant_set.id ? result.variant_set : set,
          ),
        );
        onPlanUpdated?.(result.plan);
        setMessage(
          result.render_enqueued
            ? "Direction applied and a render was queued for the new version."
            : "Direction applied. A new plan version is ready to preview.",
        );
      } catch (error) {
        setMessage((error as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [
      activeSet,
      activePlan,
      fetchLatestPlanRevision,
      onPlanUpdated,
      projectId,
      workspaceId,
    ],
  );

  return (
    <section
      data-testid="creative-variants"
      className="rounded-neu-xl bg-surface p-4 shadow-neu-raised-lg"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Sparkles className="size-4 text-primary" strokeWidth={2} />
            <h3 className="text-sm font-bold tracking-tight text-foreground">
              Creative variant testing
            </h3>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Compare deliberate directions before choosing what to produce.
          </p>
        </div>
        {sets.length > 1 ? (
          <select
            aria-label="Saved creative test sets"
            className="min-h-9 max-w-full rounded-neu border border-control bg-surface px-2 text-xs font-semibold text-foreground shadow-neu-inset-sm"
            value={activeSet?.id ?? ""}
            onChange={(event) => setActiveSetId(event.target.value)}
          >
            {sets.map((set) => (
              <option key={set.id} value={set.id}>
                {set.name} · {set.variants.length} directions
              </option>
            ))}
          </select>
        ) : null}
      </div>

      <p className="mt-3 rounded-neu bg-surface px-3 py-2 text-xs font-medium text-foreground shadow-neu-inset-sm">
        Compare directions, then apply one — applying creates a new plan version
        from that variant (optionally rendering it straight away).
      </p>

      <div className="mt-4 space-y-3">
        <div>
          <p className="text-xs font-semibold text-muted-foreground">
            Test dimensions
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {DIMENSIONS.map((dimension) => {
              const selected = dimensions.includes(dimension.id);
              return (
                <button
                  key={dimension.id}
                  type="button"
                  aria-pressed={selected}
                  className={cn(
                    "min-h-9 rounded-full px-3 text-xs font-semibold transition-shadow duration-200",
                    selected
                      ? "bg-primary text-primary-foreground shadow-neu-glow"
                      : "bg-surface text-foreground shadow-neu-raised-sm hover:shadow-neu-raised active:shadow-neu-pressed",
                  )}
                  onClick={() => toggleDimension(dimension.id)}
                >
                  {dimension.label}
                </button>
              );
            })}
          </div>
        </div>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <label className="grid gap-1 text-xs font-semibold text-foreground">
            Versions to compare
            <select
              className="min-h-9 rounded-neu border border-control bg-surface px-2 text-xs font-semibold text-foreground shadow-neu-inset-sm"
              value={variantCount}
              onChange={(event) =>
                setVariantCount(
                  Math.max(dimensions.length, Number(event.target.value)),
                )
              }
            >
              {[1, 2, 3, 4, 5, 6].map((count) => (
                <option
                  key={count}
                  value={count}
                  disabled={count < dimensions.length}
                >
                  {count}
                </option>
              ))}
            </select>
          </label>
          <Button
            disabled={busy || !activePlan || !dimensions.length}
            className="min-h-9 text-xs font-semibold"
            onClick={() => void generateSet()}
          >
            {busy ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Sparkles className="size-3.5" />
            )}
            Generate review set
          </Button>
        </div>
        {!activePlan ? (
          <p className="text-xs font-medium text-danger">
            Create or activate an edit plan before generating comparison
            directions.
          </p>
        ) : null}
      </div>

      {message ? (
        <p className="mt-3 text-xs font-medium text-foreground">{message}</p>
      ) : null}
      {loading ? (
        <div className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Loading saved tests…
        </div>
      ) : null}
      {activeSet ? (
        <div className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs font-semibold text-foreground">
              {activeSet.name} · plan revision {activeSet.source_plan_revision}
            </p>
            <span className="text-xs font-semibold text-muted-foreground">
              {activeSet.review_status === "applied"
                ? "direction applied to the edit"
                : activeSet.review_status === "selected"
                  ? "preferred direction selected"
                  : "awaiting a preferred direction"}
            </span>
          </div>
          <div className="mt-3 grid gap-3 xl:grid-cols-2">
            {activeSet.variants.map((variant) => {
              const selected = activeSet.selected_variant_id === variant.id;
              const applied = activeSet.applied_variant_id === variant.id;
              return (
                <article
                  key={variant.id}
                  className={cn(
                    "rounded-neu-lg bg-surface p-3",
                    selected
                      ? "shadow-neu-glow ring-2 ring-primary/50"
                      : "shadow-neu-raised",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-xs font-bold text-foreground">
                        {variant.label}
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        Primary:{" "}
                        {variant.primary_dimension.replaceAll("_", " ")}
                      </p>
                    </div>
                    {selected ? (
                      <span className="inline-flex items-center gap-1 text-xs font-semibold text-primary">
                        <Check className="size-3.5" /> Preferred
                      </span>
                    ) : null}
                  </div>
                  <dl className="mt-3 grid gap-2 text-xs">
                    <VariantDetail label="Hook" value={variant.hook.text} />
                    <VariantDetail
                      label="Captions"
                      value={`${variant.caption.label} · ${variant.caption.preset}`}
                    />
                    <VariantDetail
                      label="B-roll"
                      value={`${variant.broll.direction} (${variant.broll.density})`}
                    />
                    <VariantDetail
                      label="Pacing"
                      value={`${variant.pacing.label} · ${variant.pacing.cut_duration_multiplier}×`}
                    />
                    <VariantDetail
                      label="Opening / thumbnail"
                      value={`${variant.opening_frame.label}: ${variant.opening_frame.frame_direction} Thumbnail: ${variant.opening_frame.thumbnail_direction}`}
                    />
                  </dl>
                  <Button
                    variant={selected ? "secondary" : "primary"}
                    disabled={busy || selected}
                    className="mt-3 min-h-8 w-full text-xs font-bold"
                    onClick={() => void selectVariant(variant.id)}
                  >
                    {selected ? "Preferred direction" : "Mark preferred"}
                  </Button>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <Button
                      variant="primary"
                      disabled={busy || !activePlan}
                      aria-label={`Apply ${variant.label} to the edit`}
                      className="min-h-8 w-full text-xs font-bold"
                      onClick={() => void applyVariant(variant.id, false)}
                    >
                      Apply direction
                    </Button>
                    <Button
                      variant="secondary"
                      disabled={busy || !activePlan}
                      aria-label={`Apply ${variant.label} and render the new version`}
                      className="min-h-8 w-full text-xs font-bold"
                      onClick={() => void applyVariant(variant.id, true)}
                    >
                      Apply &amp; render
                    </Button>
                  </div>
                  {applied ? (
                    <p className="mt-2 text-xs font-semibold text-primary">
                      Applied to the edit (new plan version).
                    </p>
                  ) : null}
                </article>
              );
            })}
          </div>
        </div>
      ) : !loading ? (
        <p className="mt-4 text-xs text-muted-foreground">
          Generate a small comparison set instead of regenerating the timeline
          blindly.
        </p>
      ) : null}
    </section>
  );
}

function VariantDetail({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[104px_1fr] gap-2">
      <dt className="font-bold text-muted-foreground">{label}</dt>
      <dd className="text-foreground">{value}</dd>
    </div>
  );
}
