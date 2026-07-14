"use client";

import React, { useEffect, useState } from "react";
import { Input, Label, Slider, PanelSection } from "../ui";
import { Button } from "../../button";
import { Check, Film, Loader2, Sparkles, X } from "lucide-react";
import type {
  BrollDensity,
  BrollSlotSummary,
  StockVideoAssetSummary,
} from "@motionly/shared";
import {
  buildApiHeaders,
  editorApiRequest,
  resolveQueuedLayer,
} from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";
import type { PreviewBrollState } from "../preview/VideoPreview";

interface BrollTabProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialSlots: BrollSlotSummary[];
  activePlanId: string | null;
  activePlanBrollDensity: BrollDensity | null;
  onSlotUpdated: (slot: BrollSlotSummary) => void;
  onSlotsUpdated: (slots: BrollSlotSummary[]) => void;
  onPreviewBrollUpdated?: (state: PreviewBrollState | null) => void;
}

const densityMap: Record<BrollDensity, number> = {
  none: 0,
  low: 33,
  medium: 66,
  high: 100,
};

function reverseDensityMap(val: number): BrollDensity {
  if (val < 25) return "none";
  if (val < 50) return "low";
  if (val < 75) return "medium";
  return "high";
}

export function BrollTab({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialSlots,
  activePlanId,
  activePlanBrollDensity,
  onSlotUpdated,
  onSlotsUpdated,
  onPreviewBrollUpdated,
}: BrollTabProps) {
  const [density, setDensity] = useState<BrollDensity>(
    activePlanBrollDensity || "medium",
  );
  const [pickerSlotId, setPickerSlotId] = useState<string | null>(null);
  const [savingSlotId, setSavingSlotId] = useState<string | null>(null);
  const [searchingSlotId, setSearchingSlotId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const pickerSlot =
    initialSlots.find((slot) => slot.id === pickerSlotId) ?? null;

  useEffect(() => {
    onPreviewBrollUpdated?.({
      density,
      slotsCount: initialSlots.filter((slot) => slot.status !== "disabled")
        .length,
    });
  }, [density, initialSlots, onPreviewBrollUpdated]);

  async function regenerateBroll() {
    if (!activePlanId) return;
    setLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{
        slots?: BrollSlotSummary[] | null;
        status: "ready" | "queued";
        status_url?: string | null;
      }>(
        `${env.apiBaseUrl}/projects/${projectId}/broll/generate`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError:
            "B-roll could not be generated. Please try another density or try again.",
          body: {
            density,
            force: true,
          },
        },
      );
      const slots = await resolveQueuedLayer({
        submission: payload,
        immediate: payload.slots,
        apiBaseUrl: env.apiBaseUrl,
        options: { workspaceId, userId, accessToken: apiAccessToken },
        load: async () => {
          const result = await editorApiRequest<{ slots: BrollSlotSummary[] }>(
            `${env.apiBaseUrl}/projects/${projectId}/broll`,
            { workspaceId, userId, accessToken: apiAccessToken },
          );
          return result.slots;
        },
        resourceLabel: "B-roll",
        onProgress: (progress) => setMessage(`Generating B-roll: ${progress}%`),
      });
      onSlotsUpdated(slots);
      setMessage(
        slots.length > 0
          ? `Created ${slots.length} B-roll slot${slots.length === 1 ? "" : "s"}.`
          : "No B-roll slots were created from the active timeline.",
      );
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function updateSlot(
    slot: BrollSlotSummary,
    assetId: string | null,
    disabled: boolean,
  ) {
    setSavingSlotId(slot.id);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{ slot: BrollSlotSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/broll/${slot.id}/swap`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "B-roll selection could not be saved.",
          body: {
            asset_id: assetId,
            disabled,
          },
        },
      );
      onSlotUpdated(payload.slot);
      setPickerSlotId(null);
      setMessage(
        disabled ? "B-roll slot disabled." : "B-roll selection saved.",
      );
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setSavingSlotId(null);
    }
  }

  async function searchSlot(slot: BrollSlotSummary) {
    setSearchingSlotId(slot.id);
    setMessage(null);
    try {
      const params = new URLSearchParams();
      if (searchQuery.trim()) params.set("query", searchQuery.trim());
      const response = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/broll/${slot.id}/search?${params}`,
        {
          headers: await buildApiHeaders({
            workspaceId,
            userId,
            accessToken: apiAccessToken,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok)
        throw new Error(payload.error?.message ?? "B-roll search failed.");
      onSlotUpdated(payload.slot);
      setMessage(
        `Found ${payload.candidates?.length ?? 0} candidates for "${payload.query}".`,
      );
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setSearchingSlotId(null);
    }
  }

  return (
    <div className="space-y-3 sm:space-y-4">
      <h2 className="sr-only">B-Roll</h2>
      <div className="card rounded-lg border-2 border-foreground p-4 sm:p-5 space-y-4 shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] bg-surface">
        <PanelSection title="Basic" defaultOpen>
          <Slider
            label="B-Roll Density"
            value={densityMap[density]}
            onChange={(val) => setDensity(reverseDensityMap(val))}
          />
          <p className="text-[10px] font-bold text-muted-foreground mt-1 font-mono">
            Density is saved when you generate B-roll slots.
          </p>
        </PanelSection>

        <PanelSection title="Selected Assets" defaultOpen>
          <div className="mb-3 flex items-center justify-between">
            <Label className="mb-0">Selected Assets</Label>
            <span className="text-[10px] font-black uppercase text-foreground bg-surface border-2 border-foreground px-2.5 py-0.5 rounded shadow-[1px_1px_0px_#000] font-mono select-none">
              {initialSlots.length} Slots
            </span>
          </div>
          <div className="space-y-3">
            {initialSlots.length === 0 ? (
              <div className="rounded border-2 border-dashed border-foreground bg-muted/20 p-4 text-sm text-foreground font-mono">
                <p className="font-bold text-foreground">
                  No B-roll slots yet.
                </p>
                <ol className="mt-2.5 list-decimal space-y-1.5 pl-4 text-xs">
                  <li>Generate an AI edit.</li>
                  <li>Create B-roll slots from the timeline.</li>
                  <li>Select or swap assets for each slot.</li>
                </ol>
              </div>
            ) : (
              initialSlots.map((slot) => (
                <SlotRow
                  key={slot.id}
                  slot={slot}
                  saving={savingSlotId === slot.id}
                  onChange={() => setPickerSlotId(slot.id)}
                  onDisable={() => updateSlot(slot, null, true)}
                  onRestore={() =>
                    updateSlot(
                      slot,
                      slot.selected_asset_id ?? slot.candidates[0]?.id ?? null,
                      false,
                    )
                  }
                />
              ))
            )}
          </div>
        </PanelSection>
      </div>

      {pickerSlot ? (
        <AssetPicker
          slot={pickerSlot}
          saving={savingSlotId === pickerSlot.id}
          searching={searchingSlotId === pickerSlot.id}
          searchQuery={searchQuery}
          onSearchQueryChange={setSearchQuery}
          onSearch={() => searchSlot(pickerSlot)}
          onClose={() => setPickerSlotId(null)}
          onSelect={(asset) => updateSlot(pickerSlot, asset.id, false)}
        />
      ) : null}

      <button
        onClick={regenerateBroll}
        disabled={loading || !activePlanId}
        aria-busy={loading}
        aria-describedby={message ? "broll-message" : undefined}
        className="w-full inline-flex items-center justify-center gap-2 h-11 rounded border-2 border-foreground bg-primary text-primary-foreground text-xs font-black hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none transition-all duration-[100ms] focus:outline-none"
      >
        {loading ? (
          <Loader2 className="size-4 animate-spin text-primary-foreground" strokeWidth={2} />
        ) : (
          <Sparkles className="size-4" strokeWidth={2} />
        )}
        <span>
          {loading
            ? "Generating, please wait"
            : initialSlots.length === 0
              ? "Generate B-roll Slots"
              : "Regenerate B-Roll"}
        </span>
      </button>
      {message ? (
        <p
          id="broll-message"
          role={
            message.toLowerCase().includes("could not") ||
            message.toLowerCase().includes("failed")
              ? "alert"
              : "status"
          }
          aria-live="polite"
          className="rounded border-2 border-foreground bg-muted p-3.5 text-xs font-bold text-foreground font-mono shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
        >
          {message}
        </p>
      ) : null}
    </div>
  );
}

function SlotRow({
  slot,
  saving,
  onChange,
  onDisable,
  onRestore,
}: {
  slot: BrollSlotSummary;
  saving: boolean;
  onChange: () => void;
  onDisable: () => void;
  onRestore: () => void;
}) {
  const disabled = slot.status === "disabled";
  const canChange = slot.candidates.length > 0;

  return (
    <div
      className={cn(
        "border-2 border-foreground bg-surface p-3 rounded transition-all duration-[100ms] relative overflow-hidden shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]",
        disabled && "opacity-60",
      )}
    >
      <div className="flex gap-3.5">
        <AssetThumb
          asset={slot.selected_asset ?? slot.candidates[0] ?? null}
          disabled={disabled}
        />
        <div className="min-w-0 flex-1 flex flex-col justify-between">
          <div>
            <p className="truncate text-xs font-black text-foreground uppercase font-mono">
              {slot.keywords?.[0] ?? slot.intent ?? "B-roll"}
            </p>
            <p className="mt-1 line-clamp-1 text-[10px] text-muted-foreground leading-relaxed font-mono">
              {slot.intent}
            </p>
          </div>
          <div className="mt-2.5 flex flex-wrap gap-2">
            <button
              onClick={onChange}
              disabled={!canChange || saving || disabled}
              aria-label={`Change B-roll asset for ${slot.intent}`}
              title={
                !canChange
                  ? "No alternatives available"
                  : "Choose B-roll alternative"
              }
              className="min-h-8 border-2 border-foreground rounded px-3 py-1 text-xs font-bold bg-surface text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[1px_1px_0px_#000] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {canChange ? "Change" : "No alternatives available"}
            </button>
            {disabled ? (
              <button
                onClick={onRestore}
                disabled={
                  saving ||
                  (!slot.selected_asset_id && slot.candidates.length === 0)
                }
                aria-label={`Restore B-roll asset for ${slot.intent}`}
                className="min-h-8 border-2 border-foreground rounded px-3 py-1 text-xs font-bold bg-surface text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[1px_1px_0px_#000] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:cursor-not-allowed disabled:opacity-50"
              >
                Restore
              </button>
            ) : (
              <button
                onClick={onDisable}
                disabled={saving}
                aria-label={`Disable B-roll asset for ${slot.intent}`}
                className="min-h-8 border-2 border-danger rounded px-3 py-1 text-xs font-bold bg-surface text-danger hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[1px_1px_0px_#000] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:cursor-not-allowed disabled:opacity-50"
              >
                Disable
              </button>
            )}
          </div>
        </div>
        {saving ? (
          <Loader2 className="size-4 animate-spin text-primary shrink-0 self-center" strokeWidth={2} />
        ) : null}
      </div>
    </div>
  );
}

function AssetPicker({
  slot,
  saving,
  searching,
  searchQuery,
  onSearchQueryChange,
  onSearch,
  onClose,
  onSelect,
}: {
  slot: BrollSlotSummary;
  saving: boolean;
  searching: boolean;
  searchQuery: string;
  onSearchQueryChange: (value: string) => void;
  onSearch: () => void;
  onClose: () => void;
  onSelect: (asset: StockVideoAssetSummary) => void;
}) {
  return (
    <div className="card rounded-lg border-4 border-foreground p-4 sm:p-5 shadow-[6px_6px_0px_#000] bg-surface relative z-10 space-y-4">
      <div className="flex items-center justify-between gap-3 border-b-2 border-foreground pb-2.5">
        <Label className="mb-0 text-xs font-black uppercase text-foreground">Choose Alternative</Label>
        <button
          onClick={onClose}
          aria-label="Close asset picker"
          className="flex size-8 items-center justify-center rounded border-2 border-foreground text-foreground bg-surface hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[1px_1px_0px_#000] transition-all"
          title="Close asset picker"
        >
          <X className="size-4" strokeWidth={2} />
        </button>
      </div>
      <div className="flex gap-2">
        <input
          aria-label="B-roll search query"
          value={searchQuery}
          onChange={(event) => onSearchQueryChange(event.target.value)}
          placeholder={slot.keywords.join(" ") || slot.intent}
          className="h-10 w-full px-3 text-xs font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] transition-all"
        />
        <button
          onClick={onSearch}
          disabled={searching}
          aria-busy={searching}
          className="inline-flex items-center justify-center h-10 text-xs px-4 font-black border-2 border-foreground bg-primary text-primary-foreground rounded hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all"
        >
          {searching ? "Searching" : "Search"}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-3 max-h-[220px] overflow-y-auto pr-1 scrollbar-thin">
        {slot.candidates.map((asset) => {
          const selected = asset.id === slot.selected_asset_id;
          return (
            <button
              key={asset.id}
              onClick={() => onSelect(asset)}
              disabled={saving || selected}
              aria-pressed={selected}
              aria-label={`${selected ? "Selected" : "Select"} B-roll asset ${asset.title}`}
              className={cn(
                "group overflow-hidden border-2 border-foreground bg-muted text-left transition-all duration-[100ms] hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] rounded disabled:cursor-not-allowed",
                selected ? "border-primary/50 ring-2 ring-primary" : "border-border",
              )}
            >
              <div className="relative aspect-video">
                <AssetPreview asset={asset} />
                {selected ? (
                  <span className="absolute right-1.5 top-1.5 flex size-5 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm">
                    <Check className="size-3" strokeWidth={2} />
                  </span>
                ) : null}
              </div>
              <p className="truncate px-2 py-1.5 text-[10px] font-black text-foreground bg-surface/80 font-mono">
                {asset.title}
              </p>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function AssetThumb({
  asset,
  disabled,
}: {
  asset: StockVideoAssetSummary | null;
  disabled?: boolean;
}) {
  return (
    <div className="flex aspect-video w-24 shrink-0 items-center justify-center overflow-hidden border-2 border-foreground bg-muted rounded shadow-[1px_1px_0px_#000]">
      {asset?.thumbnail_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={asset.thumbnail_url}
          alt={asset.title}
          className={cn("h-full w-full object-cover", disabled && "grayscale")}
        />
      ) : (
        <Film className="size-4 text-muted-foreground/40" strokeWidth={1.5} />
      )}
    </div>
  );
}

function AssetPreview({ asset }: { asset: StockVideoAssetSummary }) {
  const playable =
    asset.preview_video_url &&
    !asset.preview_video_url.startsWith("fixture://") &&
    !asset.preview_video_url.startsWith("supabase://");
  return (
    <div className="flex h-full w-full items-center justify-center overflow-hidden bg-muted">
      {playable ? (
        <video
          src={asset.preview_video_url ?? undefined}
          poster={asset.thumbnail_url ?? undefined}
          muted
          playsInline
          preload="metadata"
          className="h-full w-full object-cover"
          onMouseEnter={(event) => void event.currentTarget.play()}
          onMouseLeave={(event) => {
            event.currentTarget.pause();
            event.currentTarget.currentTime = 0;
          }}
        />
      ) : (
        <AssetThumb asset={asset} />
      )}
    </div>
  );
}
