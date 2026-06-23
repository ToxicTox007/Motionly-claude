# Renderer feature support

This matrix is the production contract used when choosing a render engine. “Native” means the engine must preserve the feature. “Approximation” means the visual result is intentionally simpler and is recorded as a downgrade in the render manifest. “Unsupported” must never be selected silently: the job is promoted to Remotion when it is available, otherwise the manifest reports the downgrade.

| Timeline feature | FFmpeg basic | Remotion premium |
| --- | --- | --- |
| Source clip trim and concatenation | Native | Native |
| Source audio mapped through cuts | Native | Native |
| Demucs vocal stem mapped through cuts | Native | Native |
| Retimed captions and B-roll | Native | Native |
| Static burned captions | Native | Native |
| `gold_shimmer`, `slide_up`, `karaoke`, `pulse` captions | Unsupported | Native |
| B-roll crossfade | Native | Native |
| B-roll slide-in | Unsupported | Native |
| Catalog music, speech ducking, limiter | Native | Native |
| Manual SFX | Native | Native |
| Manual music elements | Unsupported | Native |
| Manual text, caption, shape and graphic layers | Native | Native |
| Manual B-roll | Native | Native |
| Manual transitions | Unsupported | Native |
| Template intro/outro cards | Unsupported | Native |
| Named color presets | Approximation | Native |
| Grain, vignette, light leak, flare, gradients | Approximation | Native |
| Ken Burns, speed ramps and impact zooms | Unsupported | Native |
| Motion keyframes and per-clip motion effects | Unsupported | Native |
| Portrait backdrop blur (ellipse/blur treatment; not segmentation) | Approximation | Approximation |
| Visual-manifest tracked auto-reframe | Approximation | Native |
| Beat-impact motion at timeline boundaries | Unsupported | Native |
| Fit/fill/custom framing and watermark | Native | Native |

Engine selection evaluates animated captions, template motion, selected B-roll/music/effects, and manual B-roll/audio/transitions. Paid eligible renders are promoted to Remotion whenever one of those features is active. If Remotion is disabled, FFmpeg remains the operational fallback and the manifest’s compatibility fields are the source of truth for downgrades.

Resolution delivery is also explicit: Free is capped at 720p, Creator and Pro at 1080p, and Agency can deliver the 4K option shown in the editor.
