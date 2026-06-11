"use client";

import React, { useState } from "react";
import {
  Film,
  Layers,
  Music2,
  Type,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Clock,
  ImageOff,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { formatMs } from "@/lib/timeline";
import type {
  BrollSlotSummary,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  MusicSelectionSummary,
  UploadedVideoSummary,
} from "@motionly/shared";

interface LeftMediaPanelProps {
  upload:           UploadedVideoSummary | null;
  thumbnailUrl:     string | null;
  activePlan:       EditPlanRecordSummary | null;
  brollSlots:       BrollSlotSummary[];
  musicSelection:   MusicSelectionSummary | null;
  captionTracks:    CaptionTrackSummary[];
}

export function LeftMediaPanel({
  upload,
  thumbnailUrl,
  activePlan,
  brollSlots,
  musicSelection,
  captionTracks,
}: LeftMediaPanelProps) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto bg-[hsl(var(--surface))] text-sm">
      <div className="pixel-grid-fine sticky top-0 z-10 border-b border-border bg-[hsl(var(--surface))] px-3 py-2">
        <p className="text-[9px] font-bold uppercase tracking-widest text-muted-foreground/60">
          Media
        </p>
      </div>

      {/* Source */}
      <MediaSection icon={Film} label="Source">
        <SourceItem upload={upload} thumbnailUrl={thumbnailUrl} />
      </MediaSection>

      {/* B-roll */}
      <MediaSection icon={Layers} label={`B-roll (${brollSlots.filter(s => s.status !== "disabled").length})`}>
        {brollSlots.length === 0 ? (
          <EmptyRow text="No B-roll yet" />
        ) : (
          brollSlots.slice(0, 6).map((slot) => (
            <BrollSlotRow key={slot.id} slot={slot} />
          ))
        )}
        {brollSlots.length > 6 && (
          <p className="px-3 py-1 text-[9px] text-muted-foreground/50">
            +{brollSlots.length - 6} more
          </p>
        )}
      </MediaSection>

      {/* Music */}
      <MediaSection icon={Music2} label="Music">
        {musicSelection?.music_track ? (
          <MusicRow selection={musicSelection} />
        ) : (
          <EmptyRow text="No music selected" />
        )}
      </MediaSection>

      {/* Captions */}
      <MediaSection
        icon={Type}
        label={`Captions (${captionTracks[0]?.segments?.length ?? 0})`}
      >
        {captionTracks[0] ? (
          <CaptionRow track={captionTracks[0]} />
        ) : (
          <EmptyRow text="No captions yet" />
        )}
      </MediaSection>

      {/* Edit plan info */}
      {activePlan && (
        <MediaSection icon={Film} label="Edit plan">
          <PlanRow plan={activePlan} />
        </MediaSection>
      )}
    </div>
  );
}

// ── Section ───────────────────────────────────────────────────────────────────

function MediaSection({
  icon: Icon,
  label,
  children,
}: {
  icon: React.ElementType;
  label: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="border-b border-border/70">
      <button
        className="flex w-full items-center gap-1.5 border-l-2 border-transparent px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-muted-foreground transition hover:border-primary/50 hover:bg-muted/60 hover:text-foreground"
        onClick={() => setOpen((v) => !v)}
      >
        <Icon className="size-3 opacity-60 shrink-0" />
        <span className="flex-1 truncate">{label}</span>
        {open
          ? <ChevronDown className="size-3 opacity-40" />
          : <ChevronRight className="size-3 opacity-40" />}
      </button>
      {open && <div className="pb-1">{children}</div>}
    </div>
  );
}

// ── Source row ────────────────────────────────────────────────────────────────

function SourceItem({
  upload,
  thumbnailUrl,
}: {
  upload: UploadedVideoSummary | null;
  thumbnailUrl: string | null;
}) {
  if (!upload) {
    return <EmptyRow text="No video uploaded" />;
  }
  return (
    <div className="flex items-center gap-2 px-3 py-1.5">
      <div className="relative size-10 shrink-0 overflow-hidden border border-border bg-zinc-800">
        {thumbnailUrl ? (
          // eslint-disable-next-line
          <img
            src={thumbnailUrl}
            alt=""
            className="h-full w-full object-cover"
          />
        ) : (
          <ImageOff className="m-auto size-4 text-muted-foreground/30" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[10px] font-medium text-foreground/80">
          {upload.originalFilename}
        </p>
        <div className="flex items-center gap-1.5 mt-0.5">
          {upload.durationMs && (
            <span className="text-[9px] text-muted-foreground/60 flex items-center gap-0.5">
              <Clock className="size-2.5" />
              {formatMs(upload.durationMs)}
            </span>
          )}
          {upload.width && upload.height && (
            <span className="text-[9px] text-muted-foreground/40">
              {upload.width}×{upload.height}
            </span>
          )}
          <StatusDot status={upload.status} />
        </div>
      </div>
    </div>
  );
}

// ── B-roll row ────────────────────────────────────────────────────────────────

function BrollSlotRow({ slot }: { slot: BrollSlotSummary }) {
  const isDisabled = slot.status === "disabled";
  const label = slot.keywords?.[0] ?? slot.intent ?? "B-roll";
  return (
    <div
      className={cn(
        "flex items-center gap-2 px-3 py-1.5",
        isDisabled && "opacity-40",
      )}
    >
      <div className="relative size-8 shrink-0 overflow-hidden border border-border bg-zinc-800">
        {slot.selected_asset?.thumbnail_url ? (
          // eslint-disable-next-line
          <img
            src={slot.selected_asset.thumbnail_url}
            alt=""
            className="h-full w-full object-cover"
          />
        ) : (
          <Layers className="m-auto size-3 text-muted-foreground/20" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[10px] font-medium text-foreground/70 capitalize">
          {label}
        </p>
        <p className="text-[9px] text-muted-foreground/40">
          {formatMs(slot.start_ms)} – {formatMs(slot.end_ms)}
        </p>
      </div>
      {isDisabled && (
        <span className="text-[8px] text-muted-foreground/40 uppercase">off</span>
      )}
    </div>
  );
}

// ── Music row ─────────────────────────────────────────────────────────────────

function MusicRow({ selection }: { selection: MusicSelectionSummary }) {
  const track = selection.music_track;
  return (
    <div className="flex items-center gap-2 px-3 py-1.5">
      <div className="flex size-8 shrink-0 items-center justify-center border border-border bg-rose-950/40">
        <Music2 className="size-3 text-rose-400" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[10px] font-medium text-foreground/80">
          {track?.title ?? "Selected track"}
        </p>
        <div className="flex gap-1.5 mt-0.5">
          {track?.bpm && (
            <span className="text-[9px] text-muted-foreground/50">{track.bpm} BPM</span>
          )}
          {track?.duration_ms && (
            <span className="text-[9px] text-muted-foreground/40">
              {formatMs(track.duration_ms)}
            </span>
          )}
        </div>
      </div>
      <div
        className="h-4 w-4 shrink-0 border border-border"
        style={{
          background: `conic-gradient(hsl(var(--primary)) ${selection.music_volume * 360}deg, transparent 0)`,
        }}
        title={`Volume: ${Math.round(selection.music_volume * 100)}%`}
      />
    </div>
  );
}

// ── Captions row ──────────────────────────────────────────────────────────────

function CaptionRow({ track }: { track: CaptionTrackSummary }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5">
      <div className="flex size-8 shrink-0 items-center justify-center border border-border bg-violet-950/40">
        <Type className="size-3 text-violet-400" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[10px] font-medium text-foreground/80">
          {track.segments?.length ?? 0} segments
        </p>
        <p className="text-[9px] capitalize text-muted-foreground/50">
          {track.preset?.replace(/_/g, " ") ?? "Custom style"}
        </p>
      </div>
      <CheckCircle2 className="size-3.5 shrink-0 text-teal-400" />
    </div>
  );
}

// ── Edit plan row ─────────────────────────────────────────────────────────────

function PlanRow({ plan }: { plan: EditPlanRecordSummary }) {
  const cuts = plan.plan?.cuts?.length ?? 0;
  return (
    <div className="flex items-center gap-2 px-3 py-1.5">
      <div className="flex size-8 shrink-0 items-center justify-center border border-border bg-teal-950/40">
        <Film className="size-3 text-teal-400" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[10px] font-medium text-foreground/80">{cuts} clips</p>
        <p className="text-[9px] text-muted-foreground/50 truncate">
          {plan.summary?.hook?.slice(0, 32) ?? "AI generated plan"}
        </p>
      </div>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function EmptyRow({ text }: { text: string }) {
  return (
    <p className="px-3 py-1.5 text-[9px] italic text-muted-foreground/40">{text}</p>
  );
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "ready"      ? "bg-teal-400" :
    status === "processing" ? "bg-amber-400 animate-pulse" :
    status === "error"      ? "bg-red-400" :
                              "bg-zinc-500";
  return (
    <span
      className={cn("inline-block size-1.5 rounded-full", color)}
      title={status}
    />
  );
}
