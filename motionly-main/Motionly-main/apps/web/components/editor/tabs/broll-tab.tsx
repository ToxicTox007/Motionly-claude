"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Input, Label, Slider, PanelSection } from "../ui";
import { Button } from "../../button";
import { Check, Film, Loader2, Sparkles, X } from "lucide-react";
import type {
  BrollDensity,
  BrollSlotSummary,
  StockVideoAssetSummary,
} from "@motionly/shared";
import { buildApiHeaders, editorApiRequest } from "@/lib/client-api";
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
  const router = useRouter();
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
      const payload = await editorApiRequest<{ slots?: BrollSlotSummary[] }>(
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
      setMessage(`B-roll queued with ${payload.slots?.length ?? 0} slots.`);
      onSlotsUpdated(payload.slots ?? []);
      if (payload.slots?.length === 0) {
        setMessage("No B-roll slots were created from the active timeline.");
      }
      router.refresh();
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
      router.refresh();
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
      router.refresh();
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
      <div className="pixel-panel space-y-3 p-3 sm:pixel-corners sm:p-4">
        <PanelSection title="Basic" defaultOpen>
          <Slider
            label="B-Roll Density"
            value={densityMap[density]}
            onChange={(val) => setDensity(reverseDensityMap(val))}
          />
          <p className="text-[10px] font-medium italic text-muted-foreground">
            Density is saved when you generate B-roll slots.
          </p>
        </PanelSection>

        <PanelSection title="Selected Assets" defaultOpen>
          <div className="mb-3 flex items-center justify-between">
            <Label className="mb-0">Selected Assets</Label>
            <span className="text-xs font-bold text-muted-foreground">
              {initialSlots.length} Slots
            </span>
          </div>
          <div className="space-y-3">
            {initialSlots.length === 0 ? (
              <div className="border border-dashed border-border bg-muted/25 p-3 text-sm text-muted-foreground">
                <p className="font-bold text-foreground">
                  No B-roll slots yet.
                </p>
                <ol className="mt-2 list-decimal space-y-1 pl-4 text-xs">
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

      <Button
        onClick={regenerateBroll}
        disabled={loading || !activePlanId}
        aria-busy={loading}
        aria-describedby={message ? "broll-message" : undefined}
        className="h-11 w-full sm:h-12"
      >
        {loading ? (
          <Loader2 className="mr-2 size-4 animate-spin" />
        ) : (
          <Sparkles className="mr-2 size-4" />
        )}
        {loading
          ? "Generating, please wait"
          : initialSlots.length === 0
            ? "Generate B-roll Slots"
            : "Regenerate B-Roll"}
      </Button>
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
          className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground"
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
        "border border-border bg-muted/30 p-2",
        disabled && "opacity-70",
      )}
    >
      <div className="flex gap-3">
        <AssetThumb
          asset={slot.selected_asset ?? slot.candidates[0] ?? null}
          disabled={disabled}
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-bold">
            {slot.keywords?.[0] ?? slot.intent ?? "B-roll"}
          </p>
          <p className="mt-1 line-clamp-2 text-[10px] text-muted-foreground">
            {slot.intent}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              onClick={onChange}
              disabled={!canChange || saving || disabled}
              aria-label={`Change B-roll asset for ${slot.intent}`}
              title={
                !canChange
                  ? "No alternatives available"
                  : "Choose B-roll alternative"
              }
              className="min-h-11 border border-border px-3 py-2 text-xs font-bold transition hover:border-primary/40 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
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
                className="min-h-11 border border-border px-3 py-2 text-xs font-bold transition hover:border-primary/40 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              >
                Restore
              </button>
            ) : (
              <button
                onClick={onDisable}
                disabled={saving}
                aria-label={`Disable B-roll asset for ${slot.intent}`}
                className="min-h-11 border border-border px-3 py-2 text-xs font-bold text-muted-foreground transition hover:border-destructive/40 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              >
                Disable
              </button>
            )}
          </div>
        </div>
        {saving ? (
          <Loader2 className="size-4 animate-spin text-primary" />
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
    <div className="pixel-panel pixel-corners space-y-3 p-4">
      <div className="flex items-center justify-between gap-3">
        <Label className="mb-0">Choose Alternative</Label>
        <button
          onClick={onClose}
          aria-label="Close asset picker"
          className="flex min-h-11 min-w-11 items-center justify-center border border-border text-muted-foreground transition hover:border-primary/40 hover:bg-muted hover:text-foreground"
          title="Close asset picker"
        >
          <X className="size-3.5" />
        </button>
      </div>
      <div className="flex gap-2">
        <Input
          aria-label="B-roll search query"
          value={searchQuery}
          onChange={(event) => onSearchQueryChange(event.target.value)}
          placeholder={slot.keywords.join(" ") || slot.intent}
          className="h-11 text-sm"
        />
        <button
          onClick={onSearch}
          disabled={searching}
          aria-busy={searching}
          className="min-h-11 border border-border px-3 text-xs font-bold transition hover:border-primary/40 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
        >
          {searching ? "Searching" : "Search"}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-3">
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
                "group overflow-hidden border bg-muted text-left transition hover:border-primary/50 disabled:cursor-not-allowed",
                selected ? "border-primary" : "border-border",
              )}
            >
              <div className="relative aspect-video">
                <AssetPreview asset={asset} />
                {selected ? (
                  <span className="absolute right-1 top-1 flex size-5 items-center justify-center bg-primary text-primary-foreground">
                    <Check className="size-3" />
                  </span>
                ) : null}
              </div>
              <p className="truncate px-2 py-1 text-[10px] font-bold">
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
    <div className="flex aspect-video w-24 shrink-0 items-center justify-center overflow-hidden border border-border bg-muted">
      {asset?.thumbnail_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={asset.thumbnail_url}
          alt={asset.title}
          className={cn("h-full w-full object-cover", disabled && "grayscale")}
        />
      ) : (
        <Film className="size-4 text-muted-foreground/40" />
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
