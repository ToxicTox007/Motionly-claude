"use client";

import type { BrollDensity, BrollSlotSummary, StockVideoAssetSummary } from "@motionly/shared";
import { Ban, ExternalLink, Film, Info, Loader2, Shuffle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/button";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

type BrollPanelProps = {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialSlots: BrollSlotSummary[];
  activePlanId: string | null;
  activePlanBrollDensity: BrollDensity | null;
  activePlanBrollCount: number;
};

export function BrollPanel({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialSlots,
  activePlanId,
  activePlanBrollDensity,
  activePlanBrollCount,
}: BrollPanelProps) {
  const [slots, setSlots] = useState(initialSlots);
  const [plan, setPlan] = useState({
    id: activePlanId,
    density: activePlanBrollDensity,
    brollCount: activePlanBrollCount,
  });
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const isLoading = phase === "loading";
  const activeVersion = useMemo(() => Math.max(...slots.map((slot) => slot.version), 0), [slots]);
  const activeSlots = slots.filter((slot) => slot.status === "ready" && slot.selected_asset_id);
  const disabledSlots = slots.filter((slot) => slot.status === "disabled");

  useEffect(() => {
    function handlePlanReady(event: Event) {
      const detail = (
        event as CustomEvent<{
          planId?: string;
          version?: number;
          brollDensity?: BrollDensity;
          brollCount?: number;
        }>
      ).detail ?? {};
      if (!detail.planId) return;
      setPlan({
        id: detail.planId,
        density: detail.brollDensity ?? null,
        brollCount: detail.brollCount ?? 0,
      });
      setSlots((current) => current.filter((slot) => slot.edit_plan_id === detail.planId));
      setMessage(`Plan v${detail.version ?? "current"} active. Select clips from its B-roll requests.`);
      setPhase("idle");
    }

    window.addEventListener("motionly:edit-plan-ready", handlePlanReady);
    return () => window.removeEventListener("motionly:edit-plan-ready", handlePlanReady);
  }, []);

  async function selectClips() {
    const force = slots.length > 0;
    if (!plan.id) {
      setPhase("error");
      setMessage("Create an AI plan with B-roll requests before generating stock clips.");
      return;
    }
    setPhase("loading");
    setMessage(force ? "Regenerating B-roll from the active plan..." : "Selecting vertical stock clips from the active plan...");
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/broll/generate`, {
        method: "POST",
        headers: await requestHeaders(workspaceId, userId, apiAccessToken),
        body: JSON.stringify({ density: "from_plan", force }),
      });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(payload.error?.message ?? "B-roll could not be generated.");
      setSlots(payload.slots ?? []);
      setPhase("ready");
      setMessage(payload.cached ? "Reused matching selections from this plan." : `B-roll ready: ${payload.job_id}`);
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  async function updateSlot(slotId: string, assetId: string | null, disabled = false) {
    setPhase("loading");
    setMessage(disabled ? "Removing this B-roll clip..." : "Replacing selected B-roll clip...");
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/broll/${slotId}/swap`, {
        method: "POST",
        headers: await requestHeaders(workspaceId, userId, apiAccessToken),
        body: JSON.stringify({ asset_id: assetId, disabled }),
      });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(payload.error?.message ?? "B-roll slot could not be updated.");
      setSlots((current) => current.map((slot) => (slot.id === payload.slot.id ? payload.slot : slot)));
      setPhase("ready");
      setMessage(disabled ? "Clip removed from this export layer." : "Replacement clip selected.");
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  return (
    <section className="rounded-lg border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Film className="size-5 text-primary" aria-hidden="true" />
            <h2 className="text-2xl font-bold">B-roll</h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {activeVersion ? `Selection version ${activeVersion}` : "Generate transcript-aligned stock inserts"}
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs font-bold uppercase text-muted-foreground">
            <span className="rounded-lg border border-border bg-white px-3 py-2">
              AI plan density: {plan.density ? plan.density : "none"}
            </span>
            <span className="rounded-lg border border-border bg-white px-3 py-2">
              Requests: {plan.brollCount}
            </span>
            {activeVersion ? (
              <span className="rounded-lg border border-border bg-white px-3 py-2">
                Active: {activeSlots.length} / Removed: {disabledSlots.length}
              </span>
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            onClick={selectClips}
            disabled={!plan.id || isLoading}
            title={!plan.id ? "Create an AI plan before generating B-roll." : undefined}
          >
            {isLoading ? <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" /> : <Shuffle className="mr-2 size-4" aria-hidden="true" />}
            {slots.length ? "Regenerate B-roll" : "Generate B-roll"}
          </Button>
        </div>
      </div>

      {message ? (
        <p className={cn("mt-4 rounded-lg border p-3 text-sm", phase === "error" ? "border-red-200 bg-red-50 text-red-800" : "border-border bg-muted text-muted-foreground")}>
          {message}
        </p>
      ) : null}

      <div className="mt-5 grid gap-3">
        {slots.length ? (
          slots.map((slot) => (
            <BrollSlotCard
              key={slot.id}
              slot={slot}
              isLoading={isLoading}
              onDisable={() => updateSlot(slot.id, null, true)}
              onSelect={(assetId) => updateSlot(slot.id, assetId)}
            />
          ))
        ) : (
          <EmptyBrollState hasPlan={Boolean(plan.id)} requestCount={plan.brollCount} />
        )}
      </div>
    </section>
  );
}

function BrollSlotCard({
  slot,
  isLoading,
  onDisable,
  onSelect,
}: {
  slot: BrollSlotSummary;
  isLoading: boolean;
  onDisable: () => void;
  onSelect: (assetId: string) => void;
}) {
  const selected = slot.selected_asset ?? slot.candidates.find((asset) => asset.id === slot.selected_asset_id) ?? null;
  return (
    <div className="rounded-lg border border-border bg-white p-4">
      <div className="grid gap-4 lg:grid-cols-[180px_minmax(0,1fr)]">
        <AssetPreview asset={selected} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate font-bold">{slot.keywords[0] ?? "B-roll slot"}</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {formatTime(slot.start_ms)} - {formatTime(slot.end_ms)} / {slot.status}
              </p>
            </div>
            <Button type="button" variant="secondary" onClick={onDisable} disabled={isLoading || slot.status === "disabled"}>
              <Ban className="mr-2 size-4" aria-hidden="true" />
              Remove
            </Button>
          </div>

          <div className="mt-3 rounded-lg border border-border bg-muted p-3 text-sm text-muted-foreground">
            <div className="flex items-start gap-2">
              <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>
                {slot.intent} Timed to transcript at {formatTime(slot.start_ms)} with {Math.round(slot.priority * 100)}% priority.
              </span>
            </div>
          </div>

          {selected ? <AssetMeta asset={selected} /> : null}

          <div className="mt-3 grid gap-2 md:grid-cols-3">
            {slot.candidates.map((asset) => (
              <button
                key={asset.id}
                type="button"
                onClick={() => onSelect(asset.id)}
                disabled={isLoading}
                className={cn(
                  "min-w-0 rounded-lg border p-3 text-left transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60",
                  slot.selected_asset_id === asset.id ? "border-teal-600 bg-teal-50" : "border-border bg-white",
                )}
              >
                <span className="block truncate text-sm font-bold">{asset.title}</span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  {asset.provider} / {asset.width}x{asset.height} / {Math.round(asset.score * 100)}%
                </span>
                <span className="mt-1 block truncate text-xs text-muted-foreground">{asset.license}</span>
              </button>
            ))}
          </div>
          {slot.error_message ? <p className="mt-3 text-sm text-red-700">{slot.error_message}</p> : null}
        </div>
      </div>
    </div>
  );
}

function AssetPreview({ asset }: { asset: StockVideoAssetSummary | null }) {
  if (!asset) {
    return (
      <div className="flex aspect-[9/16] items-center justify-center rounded-lg border border-border bg-muted text-xs font-bold uppercase text-muted-foreground">
        No clip
      </div>
    );
  }
  if (asset.preview_video_url && !asset.preview_video_url.startsWith("fixture://")) {
    return (
      <video
        className="aspect-[9/16] w-full rounded-lg border border-border bg-black object-cover"
        src={asset.preview_video_url}
        poster={asset.thumbnail_url ?? undefined}
        muted
        controls
        playsInline
      />
    );
  }
  if (asset.thumbnail_url) {
    return (
      <img
        className="aspect-[9/16] w-full rounded-lg border border-border bg-muted object-cover"
        src={asset.thumbnail_url}
        alt=""
      />
    );
  }
  return (
    <div className="flex aspect-[9/16] items-center justify-center rounded-lg border border-border bg-muted p-3 text-center text-xs font-bold text-muted-foreground">
      {asset.title}
    </div>
  );
}

function AssetMeta({ asset }: { asset: StockVideoAssetSummary }) {
  const source = sourceUrl(asset);
  return (
    <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
      <span className="rounded-lg border border-border bg-white px-2 py-1">{asset.provider}</span>
      <span className="rounded-lg border border-border bg-white px-2 py-1">{asset.license}</span>
      <span className="rounded-lg border border-border bg-white px-2 py-1">{Math.round(asset.duration_ms / 1000)}s</span>
      {source ? (
        <a className="inline-flex items-center gap-1 rounded-lg border border-border bg-white px-2 py-1 hover:bg-muted" href={source} target="_blank" rel="noreferrer">
          Source
          <ExternalLink className="size-3" aria-hidden="true" />
        </a>
      ) : null}
    </div>
  );
}

function EmptyBrollState({ hasPlan, requestCount }: { hasPlan: boolean; requestCount: number }) {
  const copy = !hasPlan
    ? "Create an AI plan first, then generate B-roll clips here."
    : requestCount === 0
      ? "The active plan has no B-roll requests. Increase B-roll density and regenerate the AI plan."
      : "Generate B-roll clips from the active AI plan.";
  return (
    <p className="rounded-lg border border-border bg-muted p-4 text-sm text-muted-foreground">
      {copy}
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

function formatTime(ms: number) {
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function sourceUrl(asset: StockVideoAssetSummary) {
  const url = asset.raw_metadata.url;
  return typeof url === "string" && url.startsWith("http") ? url : null;
}
