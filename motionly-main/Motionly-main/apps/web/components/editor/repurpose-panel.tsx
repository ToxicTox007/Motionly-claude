"use client";

import type { Dispatch, SetStateAction } from "react";
import type {
  DurationPreset,
  ExportFitMode,
  ExportFraming,
  OutputAspect,
  PlatformPreset,
} from "@motionly/shared";
import { Label, Select } from "./ui";

export type RepurposeTarget = {
  platform: PlatformPreset;
  durationPreset: DurationPreset;
  includeCaptions: boolean;
  hookText: string;
  framing: ExportFraming;
};

type PlatformProfile = {
  platform: Exclude<PlatformPreset, "custom">;
  label: string;
  aspect: OutputAspect;
  durationPreset: DurationPreset;
  durationRecommendation: string;
  hookSuggestion: string;
  captionGuidance: string;
  cropGuidance: string;
};

const profiles: PlatformProfile[] = [
  {
    platform: "instagram",
    label: "Instagram Reel",
    aspect: "9:16",
    durationPreset: "medium",
    durationRecommendation: "30s recommended",
    hookSuggestion: "Lead with the clearest visual payoff in the first second.",
    captionGuidance: "Large, two-line captions; keep the lower third clear.",
    cropGuidance: "Keep the speaker centered with headroom for the Reel UI.",
  },
  {
    platform: "tiktok",
    label: "TikTok",
    aspect: "9:16",
    durationPreset: "short",
    durationRecommendation: "15s recommended",
    hookSuggestion: "Open with a bold claim or pattern interrupt.",
    captionGuidance: "Fast, high-contrast captions that reinforce each beat.",
    cropGuidance: "Center the subject and leave extra space at the bottom.",
  },
  {
    platform: "shorts",
    label: "YouTube Shorts",
    aspect: "9:16",
    durationPreset: "long",
    durationRecommendation: "Up to 60s",
    hookSuggestion: "Show the payoff first, then reveal how it happened.",
    captionGuidance:
      "Readable captions with enough time for search-friendly context.",
    cropGuidance: "Use a steady center crop with room for titles at the top.",
  },
  {
    platform: "linkedin",
    label: "LinkedIn",
    aspect: "4:5",
    durationPreset: "medium",
    durationRecommendation: "30s recommended",
    hookSuggestion:
      "Start with the practical business takeaway before the story.",
    captionGuidance:
      "Clean, restrained captions that support professional viewing.",
    cropGuidance:
      "Use a 4:5 portrait crop that keeps the speaker and context visible.",
  },
  {
    platform: "youtube_landscape",
    label: "YouTube Landscape",
    aspect: "16:9",
    durationPreset: "long",
    durationRecommendation: "Up to 60s",
    hookSuggestion: "State the outcome, then give viewers a reason to stay.",
    captionGuidance:
      "Optional captions; reserve space for chapter or title graphics.",
    cropGuidance: "Use the wider frame to retain context beside the speaker.",
  },
  {
    platform: "instagram_post",
    label: "Instagram Post",
    aspect: "4:5",
    durationPreset: "short",
    durationRecommendation: "15s recommended",
    hookSuggestion: "Make the first line understandable without sound.",
    captionGuidance: "Keep captions concise and away from the bottom controls.",
    cropGuidance: "Frame the subject large enough to read in the feed.",
  },
  {
    platform: "x_landscape",
    label: "X Landscape",
    aspect: "16:9",
    durationPreset: "short",
    durationRecommendation: "15s recommended",
    hookSuggestion: "Use a concise, timely point that earns the pause.",
    captionGuidance:
      "Use short captions that remain readable in a compact feed.",
    cropGuidance:
      "Keep the main subject in the center third of the wider frame.",
  },
];

const durationOptions: { value: DurationPreset; label: string }[] = [
  { value: "short", label: "15 seconds · quick" },
  { value: "medium", label: "30 seconds · standard" },
  { value: "long", label: "60 seconds · extended" },
];

const fitModeOptions: { value: ExportFitMode; label: string }[] = [
  { value: "fill", label: "Fill / crop" },
  { value: "fit", label: "Fit with padding" },
  { value: "custom", label: "Custom crop" },
];

const profileByPlatform = new Map(
  profiles.map((profile) => [profile.platform, profile]),
);

export function createRepurposeTarget(
  platform: Exclude<PlatformPreset, "custom">,
  includeCaptions = true,
): RepurposeTarget {
  const profile = profileByPlatform.get(platform);
  if (!profile) throw new Error(`No Repurpose profile exists for ${platform}.`);
  return {
    platform,
    durationPreset: profile.durationPreset,
    includeCaptions,
    hookText: profile.hookSuggestion,
    framing: {
      output_aspect: profile.aspect,
      fit_mode: "fill",
      crop: { x: 0.5, y: 0.5, zoom: 1 },
      padding_color: "#000000",
    },
  };
}

export function RepurposePanel({
  targets,
  onTargetsChange,
  captionTrackId,
  onPreview,
}: {
  targets: RepurposeTarget[];
  onTargetsChange: Dispatch<SetStateAction<RepurposeTarget[]>>;
  captionTrackId?: string | null;
  onPreview: (target: RepurposeTarget) => void;
}) {
  const selectedPlatforms = new Set(targets.map((target) => target.platform));
  const targetLimitReached = targets.length >= 6;

  function updateTarget(
    platform: PlatformPreset,
    updater: (target: RepurposeTarget) => RepurposeTarget,
  ) {
    onTargetsChange((current) =>
      current.map((target) =>
        target.platform === platform ? updater(target) : target,
      ),
    );
  }

  return (
    <div className="mt-4 space-y-4 border-t-2 border-foreground/20 pt-4">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Label className="mb-0">Repurpose this source</Label>
          <span className="rounded border border-foreground/40 px-2 py-0.5 font-mono text-[10px] font-bold text-muted-foreground">
            {targets.length}/6 outputs
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          Pick platforms, then tailor the first hook, captions, crop, and
          recommended cut length for each output.
        </p>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {profiles.map((profile) => {
          const selected = selectedPlatforms.has(profile.platform);
          return (
            <button
              key={profile.platform}
              type="button"
              aria-pressed={selected}
              disabled={!selected && targetLimitReached}
              onClick={() => {
                onTargetsChange((current) =>
                  selected
                    ? current.filter(
                        (target) => target.platform !== profile.platform,
                      )
                    : [...current, createRepurposeTarget(profile.platform)],
                );
              }}
              className={`flex min-h-[48px] items-center justify-between gap-3 rounded-md border-2 px-3 py-2 text-left text-xs font-bold transition-all shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none disabled:cursor-not-allowed disabled:opacity-45 ${
                selected
                  ? "border-foreground bg-primary text-primary-foreground"
                  : "border-foreground/30 bg-surface text-foreground hover:border-foreground"
              }`}
            >
              <span>{profile.label}</span>
              <span className="font-mono text-[10px]">
                {selected ? "Selected" : profile.durationRecommendation}
              </span>
            </button>
          );
        })}
      </div>

      {targets.length ? (
        <div className="space-y-3">
          {targets.map((target) => {
            const profile = profileByPlatform.get(
              target.platform as Exclude<PlatformPreset, "custom">,
            );
            if (!profile) return null;
            const inputId = `repurpose-hook-${target.platform}`;
            return (
              <article
                key={target.platform}
                className="space-y-3 rounded-md border-2 border-foreground bg-muted/35 p-3"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h4 className="text-sm font-black">{profile.label}</h4>
                    <p className="font-mono text-[10px] text-muted-foreground">
                      {profile.aspect} · {profile.durationRecommendation}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => onPreview(target)}
                    className="min-h-9 rounded border-2 border-foreground bg-surface px-2.5 text-[10px] font-black uppercase shadow-[1px_1px_0px_#000] transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] active:translate-x-0 active:translate-y-0 active:shadow-none dark:shadow-[1px_1px_0px_#fff]"
                  >
                    Preview crop
                  </button>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label className="mb-0">Cut length</Label>
                    <Select
                      id={`repurpose-duration-${target.platform}`}
                      label={`${profile.label} cut length`}
                      value={target.durationPreset}
                      onChange={(value) =>
                        updateTarget(target.platform, (current) => ({
                          ...current,
                          durationPreset: value as DurationPreset,
                        }))
                      }
                      options={durationOptions}
                    />
                    <p className="text-[10px] text-muted-foreground">
                      Recommended: {profile.durationRecommendation}
                    </p>
                  </div>
                  <label className="flex min-h-11 items-center justify-between gap-3 rounded-md border-2 border-foreground/40 bg-surface px-3 text-xs font-bold">
                    <span>
                      <span className="block">Burn captions</span>
                      <span className="block text-[10px] font-medium text-muted-foreground">
                        {profile.captionGuidance}
                      </span>
                    </span>
                    <input
                      type="checkbox"
                      aria-label={`Burn captions into ${profile.label} output`}
                      checked={target.includeCaptions}
                      onChange={(event) =>
                        updateTarget(target.platform, (current) => ({
                          ...current,
                          includeCaptions: event.target.checked,
                        }))
                      }
                      className="size-4 shrink-0 accent-primary"
                    />
                  </label>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor={inputId} className="mb-0">
                    Opening hook
                  </Label>
                  <input
                    id={inputId}
                    value={target.hookText}
                    maxLength={160}
                    onChange={(event) =>
                      updateTarget(target.platform, (current) => ({
                        ...current,
                        hookText: event.target.value,
                      }))
                    }
                    className="flex min-h-11 w-full rounded-md border-2 border-foreground bg-surface px-3 text-xs font-medium text-foreground outline-none transition-shadow focus:shadow-[2px_2px_0px_#000] dark:focus:shadow-[2px_2px_0px_#fff]"
                  />
                  <p className="text-[10px] text-muted-foreground">
                    Rendered as an opening text layer for the first three
                    seconds.
                  </p>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label className="mb-0">Frame treatment</Label>
                    <Select
                      id={`repurpose-fit-${target.platform}`}
                      label={`${profile.label} frame treatment`}
                      value={target.framing.fit_mode}
                      onChange={(value) =>
                        updateTarget(target.platform, (current) => ({
                          ...current,
                          framing: {
                            ...current.framing,
                            fit_mode: value as ExportFitMode,
                          },
                        }))
                      }
                      options={fitModeOptions}
                    />
                    <p className="text-[10px] text-muted-foreground">
                      {profile.cropGuidance}
                    </p>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="space-y-1 text-[10px] font-bold">
                      Horizontal crop {Math.round(target.framing.crop.x * 100)}%
                      <input
                        type="range"
                        min="0"
                        max="100"
                        value={Math.round(target.framing.crop.x * 100)}
                        aria-label={`${profile.label} horizontal crop`}
                        onChange={(event) =>
                          updateTarget(target.platform, (current) => ({
                            ...current,
                            framing: {
                              ...current.framing,
                              fit_mode: "custom",
                              crop: {
                                ...current.framing.crop,
                                x: Number(event.target.value) / 100,
                              },
                            },
                          }))
                        }
                        className="w-full accent-primary"
                      />
                    </label>
                    <label className="space-y-1 text-[10px] font-bold">
                      Vertical crop {Math.round(target.framing.crop.y * 100)}%
                      <input
                        type="range"
                        min="0"
                        max="100"
                        value={Math.round(target.framing.crop.y * 100)}
                        aria-label={`${profile.label} vertical crop`}
                        onChange={(event) =>
                          updateTarget(target.platform, (current) => ({
                            ...current,
                            framing: {
                              ...current.framing,
                              fit_mode: "custom",
                              crop: {
                                ...current.framing.crop,
                                y: Number(event.target.value) / 100,
                              },
                            },
                          }))
                        }
                        className="w-full accent-primary"
                      />
                    </label>
                  </div>
                </div>

                {target.includeCaptions && !captionTrackId ? (
                  <p
                    role="alert"
                    className="rounded border border-amber-500/70 bg-amber-500/10 p-2 text-[10px] font-bold text-amber-800 dark:text-amber-200"
                  >
                    Generate captions before creating this captioned output, or
                    turn captions off for this platform.
                  </p>
                ) : null}
              </article>
            );
          })}
        </div>
      ) : (
        <p className="rounded-md border-2 border-dashed border-foreground/40 p-4 text-center text-xs text-muted-foreground">
          Select at least one platform to create tailored outputs.
        </p>
      )}
    </div>
  );
}
