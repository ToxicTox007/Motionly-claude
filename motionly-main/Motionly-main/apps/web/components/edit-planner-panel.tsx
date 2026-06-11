"use client";

import type { BrollDensity, EditPlanRecordSummary, EditPlanStyleKey } from "@motionly/shared";
import { Check, Code2, Loader2, RotateCcw, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/button";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

type EditPlannerPanelProps = {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialPlans: EditPlanRecordSummary[];
  activePlanId: string | null;
  hasTranscript: boolean;
};

const styles: { key: EditPlanStyleKey; label: string; description: string }[] = [
  { key: "luxury", label: "Luxury", description: "Warm, smooth, premium" },
  { key: "founder", label: "Founder", description: "Clean, direct, professional" },
  { key: "travel", label: "Travel", description: "Vibrant, energetic, immersive" },
  { key: "fashion", label: "Fashion", description: "Editorial, dramatic, contrasty" },
  { key: "podcast", label: "Podcast", description: "Speaker-first, quote-friendly" },
];

const densities: BrollDensity[] = ["none", "low", "medium", "high"];

export function EditPlannerPanel({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialPlans,
  activePlanId,
  hasTranscript,
}: EditPlannerPanelProps) {
  const initialActivePlan = pickActivePlan(initialPlans, activePlanId);
  const [plans, setPlans] = useState(initialPlans);
  const [activeId, setActiveId] = useState(activePlanId);
  const [style, setStyle] = useState<EditPlanStyleKey>(initialActivePlan?.style ?? "luxury");
  const [targetDuration, setTargetDuration] = useState(initialActivePlan?.target_duration_s ?? 35);
  const [density, setDensity] = useState<BrollDensity>(initialActivePlan?.broll_density ?? "medium");
  const [prompt, setPrompt] = useState(initialActivePlan?.prompt ?? "");
  const [feedback, setFeedback] = useState(initialActivePlan?.feedback ?? "");
  const [showJson, setShowJson] = useState(false);
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);

  const activePlan = useMemo(
    () => plans.find((plan) => plan.id === activeId) ?? plans.find((plan) => plan.is_active) ?? plans[0] ?? null,
    [activeId, plans],
  );
  const isRunning = phase === "loading";
  const canPlan = hasTranscript && !isRunning;
  const canRegenerate = Boolean(activePlan) && hasTranscript && !isRunning;
  const planButtonTitle = !hasTranscript
    ? "Generate a transcript before creating an AI edit plan."
    : undefined;

  useEffect(() => {
    if (!activePlan) return;
    applyPlanControls(activePlan, {
      setStyle,
      setTargetDuration,
      setDensity,
      setPrompt,
      setFeedback,
    });
  }, [activePlan?.id]);

  async function generatePlan(force = false) {
    setPhase("loading");
    setMessage(force ? "Regenerating the AI edit plan..." : "Creating an AI edit plan from the transcript...");
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
        method: "POST",
        headers: await requestHeaders(workspaceId, userId, apiAccessToken),
        body: JSON.stringify({
          prompt,
          feedback: feedback || null,
          style,
          target_duration_s: targetDuration,
          broll_density: density,
          force,
        }),
      });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(payload.error?.message ?? "Edit plan could not be generated.");
      setPlans((current) => [payload.plan, ...current.filter((plan) => plan.id !== payload.plan.id)]);
      setActiveId(payload.plan.id);
      announceActivePlan(payload.plan);
      setPhase("ready");
      setMessage(payload.cached ? "Reused matching plan from cache." : `Plan v${payload.plan.version} ready.`);
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  async function activate(planId: string) {
    setPhase("loading");
    setMessage("Restoring plan version...");
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/plans/${planId}/activate`, {
        method: "POST",
        headers: await requestHeaders(workspaceId, userId, apiAccessToken),
      });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(payload.error?.message ?? "Plan could not be restored.");
      setPlans((current) => current.map((plan) => ({ ...plan, is_active: plan.id === payload.plan.id })));
      setActiveId(payload.plan.id);
      announceActivePlan(payload.plan);
      setPhase("ready");
      setMessage(`Version ${payload.plan.version} is active.`);
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  return (
    <section className="grid gap-5 xl:grid-cols-[minmax(320px,0.85fr)_minmax(0,1.15fr)]">
      <div className="rounded-lg border border-border bg-surface p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-bold">Creative Direction</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              {activePlan ? `Version ${activePlan.version} active` : "No edit plan yet"}
            </p>
          </div>
          <Button type="button" onClick={() => generatePlan(false)} disabled={!canPlan} title={planButtonTitle}>
            {isRunning ? <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" /> : <Wand2 className="mr-2 size-4" aria-hidden="true" />}
            Create AI plan
          </Button>
        </div>

        <label className="mt-5 block text-sm font-bold text-muted-foreground" htmlFor="planner-prompt">
          Creative prompt
        </label>
        <textarea
          id="planner-prompt"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          className="mt-2 min-h-28 w-full resize-y rounded-lg border border-border bg-white p-3 text-sm outline-none transition focus:border-primary"
          placeholder="Make this sharper for founders, preserve the educational points."
          maxLength={1200}
        />

        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {styles.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setStyle(item.key)}
              className={cn(
                "rounded-lg border p-3 text-left transition",
                style === item.key ? "border-teal-600 bg-teal-50 text-teal-950" : "border-border bg-white hover:bg-muted",
              )}
            >
              <span className="block text-sm font-bold">{item.label}</span>
              <span className="mt-1 block text-xs text-muted-foreground">{item.description}</span>
            </button>
          ))}
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-bold text-muted-foreground">
            Target duration, seconds
            <input
              type="number"
              min={10}
              max={90}
              value={targetDuration}
              onChange={(event) => setTargetDuration(normalizeDurationInput(event.target.value))}
              className="mt-2 w-full rounded-lg border border-border bg-white p-2 text-foreground outline-none focus:border-primary"
            />
          </label>
          <div>
            <p className="text-sm font-bold text-muted-foreground">B-roll density</p>
            <div className="mt-2 grid grid-cols-4 overflow-hidden rounded-lg border border-border">
              {densities.map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setDensity(item)}
                  className={cn(
                    "px-2 py-2 text-xs font-bold capitalize transition",
                    density === item ? "bg-slate-950 text-white" : "bg-white text-muted-foreground hover:bg-muted",
                  )}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>
        </div>

        <label className="mt-5 block text-sm font-bold text-muted-foreground" htmlFor="planner-feedback">
          Plan feedback
        </label>
        <textarea
          id="planner-feedback"
          value={feedback}
          onChange={(event) => setFeedback(event.target.value)}
          className="mt-2 min-h-20 w-full resize-y rounded-lg border border-border bg-white p-3 text-sm outline-none transition focus:border-primary"
          placeholder="Use fewer zooms, add more premium B-roll."
          maxLength={800}
        />
        <Button className="mt-4 w-full" type="button" variant="secondary" onClick={() => generatePlan(true)} disabled={!canRegenerate} title={!activePlan ? "Create an AI plan before regenerating it." : planButtonTitle}>
          {isRunning ? <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" /> : <RotateCcw className="mr-2 size-4" aria-hidden="true" />}
          Regenerate AI plan
        </Button>

        {message ? (
          <p className={cn("mt-4 rounded-lg border p-3 text-sm", phase === "error" ? "border-red-200 bg-red-50 text-red-800" : "border-border bg-muted text-muted-foreground")}>
            {message}
          </p>
        ) : null}
      </div>

      <div className="space-y-5">
        <div className="rounded-lg border border-border bg-surface p-5">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Sparkles className="size-4 text-primary" aria-hidden="true" />
              <h3 className="text-sm font-bold uppercase text-muted-foreground">Plan Summary</h3>
            </div>
            <button
              type="button"
              onClick={() => setShowJson((current) => !current)}
              className="inline-flex size-9 items-center justify-center rounded-lg border border-border text-muted-foreground transition hover:bg-muted"
              aria-label="Toggle raw edit plan JSON"
              title="Toggle raw edit plan JSON"
            >
              <Code2 className="size-4" aria-hidden="true" />
            </button>
          </div>
          {activePlan ? <PlanSummary plan={activePlan} showJson={showJson} /> : <EmptyPlan hasTranscript={hasTranscript} />}
        </div>

        {plans.length ? (
          <div className="rounded-lg border border-border bg-surface p-5">
            <h3 className="text-sm font-bold uppercase text-muted-foreground">Versions</h3>
            <div className="mt-3 space-y-2">
              {plans.map((plan) => (
                <button
                  key={plan.id}
                  type="button"
                  onClick={() => activate(plan.id)}
                  className="flex w-full items-center justify-between gap-3 rounded-lg border border-border bg-white p-3 text-left text-sm transition hover:bg-muted"
                >
                  <span>
                    <span className="font-bold">Version {plan.version}</span>
                    <span className="ml-2 text-muted-foreground">{plan.style}</span>
                  </span>
                  {plan.id === activeId ? <Check className="size-4 text-teal-700" aria-hidden="true" /> : null}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function PlanSummary({ plan, showJson }: { plan: EditPlanRecordSummary; showJson: boolean }) {
  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Metric label="Duration" value={`${plan.summary.duration_s}s`} />
        <Metric label="Cuts" value={String(plan.summary.cut_count)} />
        <Metric label="B-roll" value={String(plan.summary.broll_count)} />
        <Metric label="Zooms" value={String(plan.summary.zoom_count)} />
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <InfoBlock label="Music" value={plan.summary.music_mood} />
        <InfoBlock label="Color" value={plan.summary.color_grade} />
      </div>
      <InfoBlock label="Source" value={plan.summary.source_topic ?? "Transcript-driven edit"} />
      {plan.summary.direction_notes?.length ? <InfoBlock label="Direction" value={plan.summary.direction_notes.join(", ")} /> : null}
      <InfoBlock label="Effects" value={plan.summary.effects.length ? plan.summary.effects.join(", ") : "None"} />
      {plan.summary.risk_flags.length ? <InfoBlock label="Risks" value={plan.summary.risk_flags.join(", ")} tone="warning" /> : null}
      <div className="rounded-lg border border-border bg-white p-3">
        <p className="text-xs font-bold uppercase text-muted-foreground">Cut Plan</p>
        <div className="mt-3 space-y-2">
          {plan.plan.cuts.map((cut, index) => (
            <div key={`${cut.start_ms}-${cut.end_ms}-${index}`} className="flex items-center justify-between gap-3 text-sm">
              <span className="font-medium">{cut.reason}</span>
              <span className="text-muted-foreground">
                {formatTime(cut.start_ms)} - {formatTime(cut.end_ms)}
              </span>
            </div>
          ))}
        </div>
      </div>
      {showJson ? (
        <pre className="max-h-80 overflow-auto rounded-lg bg-slate-950 p-4 text-xs leading-5 text-slate-100">
          {JSON.stringify(plan.plan, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-white p-3">
      <p className="text-xs font-bold uppercase text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-bold">{value}</p>
    </div>
  );
}

function InfoBlock({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "warning" }) {
  return (
    <div className={cn("rounded-lg border p-3 text-sm", tone === "warning" ? "border-amber-200 bg-amber-50 text-amber-950" : "border-border bg-white")}>
      <p className="text-xs font-bold uppercase text-muted-foreground">{label}</p>
      <p className="mt-1 font-medium">{value}</p>
    </div>
  );
}

function EmptyPlan({ hasTranscript }: { hasTranscript: boolean }) {
  return (
    <p className="mt-4 rounded-lg border border-border bg-muted p-4 text-sm text-muted-foreground">
      {hasTranscript ? "Create an AI plan to drive cuts, B-roll, zooms, music, effects, and color." : "Generate a transcript first, then create an AI plan."}
    </p>
  );
}

function requestHeaders(workspaceId: string, userId: string, apiAccessToken: string) {
  return buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken });
}

async function readJson(response: Response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

function announceActivePlan(plan: EditPlanRecordSummary) {
  window.dispatchEvent(
    new CustomEvent("motionly:edit-plan-ready", {
      detail: {
        planId: plan.id,
        version: plan.version,
        style: plan.style,
        brollDensity: plan.broll_density,
        brollCount: plan.summary.broll_count,
        musicMood: plan.summary.music_mood,
        colorGrade: plan.summary.color_grade,
      },
    }),
  );
}

function pickActivePlan(plans: EditPlanRecordSummary[], activePlanId: string | null) {
  return (
    plans.find((plan) => plan.id === activePlanId) ??
    plans.find((plan) => plan.is_active) ??
    plans[0] ??
    null
  );
}

function applyPlanControls(
  plan: EditPlanRecordSummary,
  setters: {
    setStyle: (value: EditPlanStyleKey) => void;
    setTargetDuration: (value: number) => void;
    setDensity: (value: BrollDensity) => void;
    setPrompt: (value: string) => void;
    setFeedback: (value: string) => void;
  },
) {
  setters.setStyle(plan.style);
  setters.setTargetDuration(plan.target_duration_s);
  setters.setDensity(plan.broll_density);
  setters.setPrompt(plan.prompt);
  setters.setFeedback(plan.feedback ?? "");
}

function normalizeDurationInput(value: string) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 35;
  return Math.max(10, Math.min(90, Math.round(parsed)));
}

function formatTime(ms: number) {
  const totalSeconds = Math.floor(ms / 1000);
  return `${Math.floor(totalSeconds / 60)}:${String(totalSeconds % 60).padStart(2, "0")}`;
}
