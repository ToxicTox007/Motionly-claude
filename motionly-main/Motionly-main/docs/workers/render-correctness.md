# Render Correctness Checks

## Chromium smoke command

Run this inside the Chromium worker image or an equivalent environment with `ffmpeg`,
`ts-node`, and `BROWSER_EXECUTABLE_PATH` set:

```bash
cd apps/renderer
pnpm render:smoke
```

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
