import { safeRendererMediaUrl } from "./manual-media-policy.js";
import type { MotionlyRenderProps } from "./types";

/**
 * Every media URL the composition can fetch, with a label for errors.
 *
 * In production the worker (apps/worker/src/remotion.mjs assembleRenderProps)
 * rewrites each of these to its loopback media proxy, which validated and
 * pinned the real upstream. The renderer re-checks that here so a props file
 * produced any other way can never make Chromium fetch an arbitrary URL
 * (cloud metadata, internal services). Manual elements are checked when they
 * render (components/ManualElements.tsx).
 */
export function renderPropsMediaUrls(
  props: MotionlyRenderProps,
): Array<{ field: string; value: unknown; allowDataAudio: boolean }> {
  const urls: Array<{
    field: string;
    value: unknown;
    allowDataAudio: boolean;
  }> = [
    {
      field: "sourceVideo.src",
      value: props.sourceVideo?.src,
      allowDataAudio: false,
    },
  ];
  for (const asset of props.assets ?? []) {
    urls.push({
      field: `assets[${asset.id}].src`,
      value: asset.src,
      allowDataAudio: asset.kind === "audio",
    });
  }
  const track = props.musicSelection?.track as
    | (Record<string, unknown> & { raw_metadata?: Record<string, unknown> })
    | null
    | undefined;
  if (track) {
    for (const key of ["full_track_url", "preview_url"]) {
      if (track[key] != null) {
        urls.push({
          field: `musicSelection.track.${key}`,
          value: track[key],
          allowDataAudio: true,
        });
      }
    }
    const metadata = track.raw_metadata ?? {};
    for (const key of ["audio_url", "download_url", "preview_url", "src"]) {
      if (metadata[key] != null) {
        urls.push({
          field: `musicSelection.track.raw_metadata.${key}`,
          value: metadata[key],
          allowDataAudio: true,
        });
      }
    }
    const sfx = Array.isArray(metadata.sfx_urls) ? metadata.sfx_urls : [];
    sfx.forEach((value, index) =>
      urls.push({
        field: `musicSelection.track.raw_metadata.sfx_urls[${index}]`,
        value,
        allowDataAudio: true,
      }),
    );
  }
  for (const stem of ["vocals", "no_vocals"] as const) {
    const src = props.sourceStems?.[stem]?.src;
    if (src != null)
      urls.push({
        field: `sourceStems.${stem}.src`,
        value: src,
        allowDataAudio: false,
      });
  }
  return urls;
}

/**
 * Refuse props whose media is not served by the worker's loopback proxy
 * (or, for audio, a bounded inline WAV). Throws with every offending field.
 */
export function assertProductionProps(props: MotionlyRenderProps): void {
  if (!props.sourceVideo?.src) {
    throw new Error("A real sourceVideo.src URL is required for rendering.");
  }
  const rejected = renderPropsMediaUrls(props)
    .filter(
      ({ value, allowDataAudio }) =>
        safeRendererMediaUrl(value, { allowDataAudio }) === null,
    )
    .map(({ field }) => field);
  if (rejected.length) {
    throw new Error(
      `Render media must come through the worker media proxy; refused: ${rejected.join(", ")}`,
    );
  }
}
