# Render Correctness Checks

## Chromium smoke command

Run this inside the Chromium worker image or an equivalent environment with `ffmpeg`,
`ts-node`, and `BROWSER_EXECUTABLE_PATH` set:

```bash
cd apps/renderer
pnpm render:smoke
```

## Preview/export parity contract

Preview and export share one source-timeline evaluator: `packages/shared/src/timeline-eval.mjs` (typed by `timeline-eval.d.mts`). It is consumed by the browser preview (`usePlaybackSync` and timeline seeking) and the Remotion renderer (`apps/renderer/src/timeline.ts`). The evaluator normalizes clips, maps output time through removed source gaps, resolves start/end boundary semantics, and computes output duration. The preview uses the canonical export FPS (`PreviewExportState.fps`, defaulting to `DEFAULT_RENDER_FPS`); it does not use the source upload FPS for frame quantization.

`packages/shared/src/timeline-golden.json` is the shared timing golden. Web and renderer tests both consume it to verify the same output-to-source samples and duration. Renderer frame goldens remain the visual export regression suite; the browser preview is not treated as a second export renderer.

The browser preview's CSS/WebGL color grade, motion effects, transitions, and subject-matte fallback are intentionally lower-cost approximations. The preview displays explicit badges such as **“Grade/motion: preview approximation”** and **“Approximate subject preview”**. These approximations must never be described as export output; the authoritative visual result is the selected export renderer's output and compatibility manifest.
The smoke command:

- generates a trivial source MP4 with `ffmpeg`
- runs the real `render-cli.ts` path
- fails fast when Chromium is missing or unusable
- asserts that the rendered output file exists and is non-empty

## Structured failure reasons

Render preflight and worker execution now classify these failures before or during render:

- `missing_source_video`
- `invalid_captions`
- `ass_compile_failed`
- `invalid_remotion_props`
- `broll_asset_unavailable`
- `music_asset_unavailable`
- `ffmpeg_missing`
- `chromium_missing`
- `temp_dir_unwritable`
- `export_bucket_unwritable`
- `render_timeout`
- `render_cancelled`

Queued renders can be canceled directly. Running premium renders poll job status and terminate
the Chromium render subprocess when the job transitions to `canceled`.
