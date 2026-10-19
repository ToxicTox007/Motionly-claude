/**
 * timeline.ts
 * -----------
 * Converts the raw backend data (edit plan, caption track, B-roll slots,
 * music selection) into a normalised TimelineState the UI components consume.
 *
 * All timing values are in milliseconds. All IDs are stable strings suitable
 * for use as React keys.
 */

import type {
  BrollSlotSummary,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  MusicSelectionSummary,
  TimelineClip,
  TimelineState,
  TimelineTrack,
} from "@motionly/shared";
export { outputMsToSourceMs } from "@motionly/shared";

// ── Track accent colours (Tailwind utility strings) ──────────────────────────
const TRACK_COLORS: Record<string, string> = {
  video: "bg-teal-500/80",
  broll: "bg-amber-500/80",
  caption: "bg-violet-500/80",
  music: "bg-rose-500/80",
  zoom: "bg-sky-500/80",
  transition: "bg-cyan-500/80",
  overlay: "bg-fuchsia-500/80",
  sfx: "bg-orange-500/80",
};

export interface BuildTimelineInput {
  upload: {
    duration_ms?: number | null;
  } | null;
  activePlan: EditPlanRecordSummary | null;
  captionTrack: CaptionTrackSummary | null;
  brollSlots: BrollSlotSummary[];
  musicSelection: MusicSelectionSummary | null;
}

export function buildTimelineState({
  upload,
  activePlan,
  captionTrack,
  brollSlots,
  musicSelection,
}: BuildTimelineInput): TimelineState {
  const document = activePlan?.plan?.timeline ?? null;
  const totalDurationMs =
    document?.duration_ms ?? upload?.duration_ms ?? 30_000;

  // ── Video track: one clip per cut in the edit plan ───────────────────────
  const cuts = document?.clips ?? activePlan?.plan?.cuts ?? [];
  const videoClips: TimelineClip[] = cuts.map((cut, idx) => ({
    id: `cut-${idx}`,
    trackKind: "video",
    startMs: "output_start_ms" in cut ? cut.output_start_ms : cut.start_ms,
    endMs: "output_end_ms" in cut ? cut.output_end_ms : cut.end_ms,
    label: cut.reason?.slice(0, 32) ?? `Clip ${idx + 1}`,
    colorClass: TRACK_COLORS.video,
    disabled: false,
    sourceRef: { kind: "cut", index: idx },
  }));

  // Fallback: if no cuts yet, show a single placeholder spanning full duration
  if (videoClips.length === 0 && totalDurationMs > 0) {
    videoClips.push({
      id: "cut-placeholder",
      trackKind: "video",
      startMs: 0,
      endMs: totalDurationMs,
      label: "Raw footage",
      colorClass: "bg-teal-800/50",
      disabled: false,
      sourceRef: { kind: "cut", index: 0 },
    });
  }

  // ── B-roll track: one clip per enabled slot ──────────────────────────────
  const brollClips: TimelineClip[] = document?.broll?.length
    ? document.broll.map((layer) => ({
        id: `broll-${layer.id}`,
        trackKind: "broll" as const,
        startMs: layer.start_ms,
        endMs: layer.end_ms,
        label: String(layer.metadata?.label ?? "B-roll").slice(0, 28),
        colorClass: TRACK_COLORS.broll,
        disabled: false,
        sourceRef: { kind: "broll_slot" as const, id: layer.id },
      }))
    : brollSlots
        .filter((s) => s.status !== "disabled")
        .map((slot) => ({
          id: `broll-${slot.id}`,
          trackKind: "broll" as const,
          startMs: slot.start_ms,
          endMs: slot.end_ms,
          label: (slot.keywords?.[0] ?? slot.intent ?? "B-roll").slice(0, 28),
          colorClass: TRACK_COLORS.broll,
          disabled: false,
          sourceRef: { kind: "broll_slot" as const, id: slot.id },
        }));

  // ── Caption track: one clip per segment ─────────────────────────────────
  const captionSegments = document?.captions?.length
    ? document.captions
    : (captionTrack?.segments ?? []);
  const captionClips: TimelineClip[] = captionSegments.map((seg) => ({
    id: `caption-${seg.id ?? seg.index}`,
    trackKind: "caption" as const,
    startMs: seg.start_ms,
    endMs: seg.end_ms,
    label: seg.text.slice(0, 32),
    colorClass: TRACK_COLORS.caption,
    disabled: false,
    sourceRef: {
      kind: "caption_segment" as const,
      id: seg.id ?? undefined,
      index: seg.index,
    },
  }));

  // ── Music track: editable regions from the music selection ───────────────
  const musicRegions = musicSelection?.regions?.length
    ? musicSelection.regions
    : musicSelection?.music_track_id
      ? [{ id: "music-0", start_ms: 0, end_ms: totalDurationMs }]
      : [];
  const musicClips: TimelineClip[] = musicRegions.map((region) => ({
    id: `music-${region.id}`,
    trackKind: "music" as const,
    startMs: region.start_ms,
    endMs: region.end_ms,
    label: musicSelection?.music_track?.title ?? "Background music",
    colorClass: TRACK_COLORS.music,
    disabled: region.disabled ?? false,
    sourceRef: { kind: "music" as const, regionId: region.id },
  }));

  const manualElements = document
    ? [...document.manual_layers, ...document.transitions]
    : (activePlan?.plan?.manual_elements ?? []);
  musicClips.push(
    ...manualElements
      .filter((element) => element.type === "music")
      .map((element) => ({
        id: `element-${element.id}`,
        trackKind: "music" as const,
        startMs: element.start_ms,
        endMs: element.end_ms,
        label: element.name,
        colorClass: TRACK_COLORS.music,
        disabled: false,
        sourceRef: { kind: "manual_element" as const, id: element.id },
      })),
  );
  const manualClips = (kind: "transition" | "overlay" | "sfx") =>
    manualElements
      .filter((element) =>
        kind === "overlay"
          ? ["text", "caption", "overlay", "shape", "graphic"].includes(
              element.type,
            ) && element.zone === "timeline"
          : element.type === kind,
      )
      .map((element) => ({
        id: `element-${element.id}`,
        trackKind: kind,
        startMs: element.start_ms,
        endMs: element.end_ms,
        label: element.name,
        colorClass: TRACK_COLORS[kind],
        disabled: false,
        sourceRef: { kind: "manual_element" as const, id: element.id },
      }));

  const tracks: TimelineTrack[] = [
    {
      kind: "video",
      label: "VIDEO",
      clips: videoClips,
      muted: false,
      locked: false,
    },
    {
      kind: "broll",
      label: "BROLL",
      clips: brollClips,
      muted: false,
      locked: false,
    },
    {
      kind: "caption",
      label: "CAPS",
      clips: captionClips,
      muted: false,
      locked: false,
    },
    {
      kind: "transition",
      label: "TRANS",
      clips: manualClips("transition"),
      muted: false,
      locked: false,
    },
    {
      kind: "overlay",
      label: "FX",
      clips: manualClips("overlay"),
      muted: false,
      locked: false,
    },
    {
      kind: "music",
      label: "MUSIC",
      clips: musicClips,
      muted: false,
      locked: false,
    },
    {
      kind: "sfx",
      label: "SFX",
      clips: manualClips("sfx"),
      muted: false,
      locked: false,
    },
  ];

  return { totalDurationMs, tracks };
}

// ── Formatting helpers used across timeline UI ────────────────────────────────

/** Format milliseconds as M:SS (e.g. 1:05) */
export function formatMs(ms: number): string {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Generate ruler tick timestamps for a given total duration. */
export function rulerTicks(
  totalMs: number,
): Array<{ ms: number; label: string }> {
  const steps = [1_000, 2_000, 5_000, 10_000, 15_000, 30_000, 60_000, 120_000];
  // Target ~8 ticks across the ruler
  const targetInterval = totalMs / 8;
  const interval =
    steps.find((s) => s >= targetInterval) ?? steps[steps.length - 1];

  const ticks: Array<{ ms: number; label: string }> = [];
  for (let ms = 0; ms <= totalMs; ms += interval) {
    ticks.push({ ms, label: formatMs(ms) });
  }
  return ticks;
}
