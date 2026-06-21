# Motionly — AI-First Video Editing System
## Product & Engineering Plan

---

## 1. Vision

Motionly is an AI-first video editing platform that removes the blank-canvas problem from video creation entirely. A user uploads raw footage; the AI analyzes, understands, and assembles a polished, short-form-optimized edit. The user then refines rather than builds. The core philosophy: **AI creates, humans perfect.**

The experience contract is simple:
```
Upload → Understand → Edit → Tweak → Export
```

Every decision the AI makes must be explainable and reversible. The AI is an editor with a point of view, not an automation black box.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CLIENT (Browser)                           │
│  ┌──────────────┐  ┌─────────────────────┐  ┌────────────────────┐ │
│  │ Upload & Ingest│  │  AI Edit Preview    │  │  Manual Edit Panel │ │
│  │  (chunked)    │  │  (live render)      │  │  (timeline + tools)│ │
│  └──────┬───────┘  └──────────┬──────────┘  └─────────┬──────────┘ │
└─────────┼────────────────────┼─────────────────────────┼────────────┘
          │ WebSocket / REST   │  Real-time preview      │ Edit ops
          ▼                    ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         API GATEWAY (FastAPI)                        │
└────────────────────────────┬────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────────┐
          ▼                  ▼                       ▼
┌──────────────────┐ ┌───────────────────┐ ┌────────────────────────┐
│  Ingestion        │ │  AI Orchestrator  │ │  Render Engine         │
│  Service          │ │  (BullMQ + Redis) │ │  (frame composition)   │
│  - upload         │ │  - job queue      │ │  - FFmpeg (standard)   │
│  - chunking       │ │  - model routing  │ │  - Remotion (premium)  │
│  - transcoding    │ │  - result merging │ │  - live preview        │
│  - storage        │ └───────┬───────────┘ └────────────────────────┘
└──────────────────┘         │
          ┌──────────────────┼──────────────────────────────┐
          ▼                  ▼                              ▼
┌──────────────────┐ ┌─────────────────────┐  ┌────────────────────────┐
│  Speech & Audio  │ │  AI Edit Planner    │  │  Edit Assembly         │
│  Analysis Module │ │  (LLM via OpenRouter│  │  Module                │
│  - Groq/Whisper  │ │  or Groq-compat)    │  │  - timeline builder    │
│    (word-level)  │ │  - EditPlan JSON    │  │  - cut logic           │
│  - transcription │ │  - style selection  │  │  - B-roll matcher      │
│    jobs queue    │ │  - b-roll requests  │  │  - caption generator   │
│  - language hint │ │  - zoom moments     │  │  - effects config      │
└──────────────────┘ └─────────────────────┘  └────────────────────────┘
          │                  │                              │
          └──────────────────┴──────────────────────────────┘
                                     │
                     ┌───────────────▼────────────────┐
                     │    Edit Manifest (JSON)         │
                     │    - EditPlan record            │
                     │    - cuts, broll, zoom moments  │
                     │    - caption highlights         │
                     │    - effects + color grade      │
                     └────────────────────────────────┘
```

### Current Stack (Shipped)

| Layer | What Exists |
|---|---|
| Monorepo | pnpm workspaces + Turborepo — `apps/web`, `apps/api`, `apps/worker`, `apps/renderer`, `packages/shared` |
| Frontend | Next.js (App Router), TypeScript, Tailwind CSS, Supabase Auth |
| Backend API | Python 3.12 + FastAPI — full repository + service layer pattern |
| Queue | BullMQ (Node.js bridge) + Redis — named queues per pipeline stage |
| Transcription | Groq Whisper-compatible HTTP endpoint (word-level, language hint support) |
| AI Edit Planner | OpenRouter LLM via HTTP (JSON schema-constrained, with fixture fallback + repair layer) |
| Renderer (standard) | FFmpeg-based render pipeline with ASS caption overlay, B-roll compositing, music/SFX mixing |
| Renderer (premium) | Remotion (`apps/renderer`) — React-based composition with animated captions, visual grades, music sync |
| B-roll | CLIP-style semantic ranking over visual manifests + Pexels/Pixabay providers; fixture provider for dev |
| Music | Licensed catalog table in Supabase + fixture provider; beat-map aware, SFX cue URLs |
| Storage | Supabase Storage (signed URLs) or local filesystem — pluggable `StorageSigner` |
| Database | Supabase / PostgreSQL + RLS; full migration history (`infra/migrations/`) |
| Billing | Stripe (subscriptions: free / creator / pro / agency) + usage ledger |
| Observability | GlitchTip (Sentry-compatible) on web + API |

---

## 3. AI Pipeline — Detailed Stages

The AI pipeline is sequential with parallelisable sub-tasks. Every stage writes to an **Edit Manifest** — a structured JSON document that represents every decision the AI makes. The manifest is the source of truth for both render and manual editing.

### Stage 0 — Ingestion & Preprocessing

**Current state:** Fully shipped.

- Chunked multipart upload via `POST /uploads` (direct-to-storage signed URL flow)
- FFmpeg-based media probe (`media_probe.py`) — extracts duration, resolution, fps, codec, audio presence
- Thumbnail generation (720p frame at 1s) via `VideoPreprocessor`
- Upload validation (`upload_validation.py`) — codec/container compatibility checks, warning codes
- Async job dispatch via `PreprocessQueue` (BullMQ) → worker calls `POST /internal/jobs/:id/run`
- Upload state machine: `created → uploading → uploaded → processing → ready / failed`
- Emits to `TranscriptionQueue` after preprocessing completes

### Stage 1 — Audio Intelligence

**Current state:** Shipped as an explicit audio-intelligence pass layered on top of transcription.

| Task | Current Implementation | Next Version Target |
|---|---|---|
| Speech transcription | Groq-compatible Whisper HTTP endpoint; word-level timestamps, expanded language hints, confidence rollups | Add provider-specific word confidence when available |
| Keyword extraction | Explicit deterministic keyword pass stored in `raw_payload.audio_intelligence` and fed into planner cache/prompting | Replace heuristic ranking with ML/LLM hybrid scorer |
| Tone / style classification | Separate audio style/tone classifier summary before planner | Fine-tune style pre-classifier on user override history |
| Speaker diarization | Heuristic speaker turn grouping plus real pyannote.audio worker provider when `PYANNOTE_AUTH_TOKEN` is configured | Tune diarization thresholds for podcast/interview layouts |
| Emotion / pacing | Pacing, pause, energy, and emotion-window summaries plus real wav2vec2 worker provider | Use fine-tuned emotion model per creator niche |
| Sound classification | Heuristic background sound summary plus real YAMNet worker provider on normalized 16 kHz audio | Map YAMNet class ids to human labels |

**Output:** `transcripts` Supabase table plus `raw_payload.audio_intelligence` — word-level records, keywords, style/tone, speech metrics, diarization, emotion/pacing windows, and sound classification.

### Stage 2 — Visual Intelligence

**Current state:** Shipped API/service/repository/worker path with a real local ML provider and fixture provider for deterministic tests/dev.

The codebase now has a visual pipeline that can run synchronously through the API or via the `visual_analysis` queue. It writes versioned manifests to `video_manifests` in Supabase/local memory. The real provider lazy-loads CLIP, BLIP-2, YOLO, MediaPipe, OpenCV, Pillow, NumPy, Torch, and Transformers:

- Frame sampling at 2 fps or at scene-change boundaries
- CLIP embeddings per frame for semantic search
- BLIP-2 natural-language captions per scene
- Object / face detection (YOLO + MediaPipe)
- Scene type classification
- Dominant color extraction per scene
- Camera motion detection (pan, tilt, static, handheld)

**Output:** `video_manifest.json` shape stored in `video_manifests.manifest` — frame samples and scene segments with semantic embeddings, captions, objects, faces, color data, camera motion, scene types, and visual importance scores. The latest ready visual manifest is now merged into edit-planner cache keys and planner prompts; fixture planning also uses visual captions/object labels to seed B-roll requests.

### Stage 3 — Style Classification

**Current state:** Shipped as a deterministic audio+visual pre-classifier feeding the planner, with LLM planning still available downstream.

The `EditPlanStyle` enum now supports the full canonical Stage 3 style set: `motivational`, `emotional`, `cinematic`, `educational`, `luxury`, `storytelling`, `dramatic`, `fast_paced`, `clean_minimal`, `creator_influencer`. Legacy request values (`founder`, `travel`, `fashion`, `podcast`) remain accepted and are disambiguated into canonical styles. The classifier reads transcript words, `raw_payload.audio_intelligence`, prompt/feedback, and the latest ready visual manifest. Its output is stored in `summary.style_classification`, included in cache keys, and sent to the HTTP edit planner prompt.

**Implemented style defaults:**

| Style Class | Triggers | Defaults |
|---|---|---|
| `motivational` | Uplifting speech, forward-looking language, upbeat pacing | High-energy cuts, warm grade, bold captions, uplift music |
| `emotional` | Emotional language, slow pacing, intimate framing | Slow push-ins, muted grade, minimal captions, soft music |
| `cinematic` | Wide shots, atmospheric audio, storytelling structure | Long cuts, film grain, color teal-orange grade, sparse text |
| `educational` | Instructional language, screen recordings, step markers | Clean cuts, bright neutral grade, label-style captions |
| `luxury` | High-quality visuals, calm tone, brand language | Slow motion, bleach bypass grade, thin elegant captions |
| `storytelling` | Narrative structure, character focus, arc present | Act-based pacing, warm grade, minimal captions |
| `dramatic` | Intense emotion, conflict language, urgency | Sharp cuts, high-contrast grade, impact captions |
| `fast-paced` | Dense speech, action, high visual variety | Sub-1s cuts, energetic transitions, beat-sync edits |
| `clean minimal` | Calm speech, single subject, clear product focus | Static reframe, clean white/neutral grade, subtle text |
| `creator / influencer` | Casual speech, direct-to-camera, personal tone | Hook cut, animated captions, social overlays, trending SFX |

The web editor includes an Auto style option that omits `style` from the request, allowing the API to classify from audio+visual context. Every canonical style is wired to planner defaults, render template selection, and effects/color-grade defaults.

### Stage 4 — B-Roll Intelligence

**Current state:** Shipped with semantic visual matching and placement safety rules.

B-roll is placed via enriched `BrollRequest` entries in the `EditPlan`. In addition to `keywords`, `start_ms`, `end_ms`, and `priority`, requests now carry `semantic_query`, CLIP-style `semantic_embedding`, source scene metadata, target `shot_type`, placement rule, and face-overlap safety flags. The B-roll engine (`broll.py` API + `broll_engine` queue job) prefers semantic query/embedding scoring over plain keyword matching, queries Pexels/Pixabay through the pluggable stock provider interface, and stores results in `stock_assets` + versioned `broll_slots`.

Current placement and ranking logic:
- B-roll count controlled by `BrollDensity` enum (none / low / medium / high)
- Keyword phrases seeded from transcript audio intelligence plus visual captions/object labels
- B-roll starts on semantically anchored cut boundaries and is re-anchored to safe visual scenes when face overlap is avoided
- Max B-roll count capped at `min(4, cuts + 3)`
- Direction text overrides: "more b-roll" adds 1 slot, "less b-roll" removes 1
- CLIP-style embedding similarity is part of candidate scoring; lexical fallback remains for providers that do not expose embeddings
- B-roll never covers speaker's face during critical speech moments when a ready visual manifest identifies high-importance face/talking-head scenes
- Shot type diversity is enforced during candidate ranking
- Max 3 consecutive B-roll cuts before returning to main subject
- Slot metadata records semantic query, embedding dimensions, source scene, selected shot type, placement rule, visual manifest signature, and selection rules

B-roll sources (pluggable `StockVideoProvider` interface):
- Pexels API ✅ (shipped)
- Pixabay API ✅ (shipped)
- User's own B-roll library (planned Phase 3)
- AI-generated video clips via Runway Gen-3 (planned Phase 5, premium tier)

### Stage 5 — Kinetic Caption System

**Current state:** Shipped with mode-aware kinetic captions across API, web preview, Remotion export, and FFmpeg fallback.

Captions are rendered as composited visual elements — not subtitle tracks. The `apps/renderer` Remotion composition renders `AnimatedCaptions` against the video with per-word timing.

**Shipped caption presets (`CaptionPresetKey`):**

| Preset Key | Look |
|---|---|
| `luxury_minimal` | Inter 600, teal accent, fade-up animation, lower placement |
| `bold_influencer` | Inter 900, uppercase, red accent, 3px stroke, POP animation |
| `vogue_white` | Georgia 700, title case, editorial feel |
| `gold_premium` | Gold accent, premium feel |
| `founder_clean` | Clean blue, professional |
| `impact_stamp` | Full-width uppercase stamp with shake, strong stroke, red active-word accent |
| `minimal_line` | Quiet single-line bottom-third with long fade and subtle underline highlights |
| `educational_card` | Rounded step card with badge, high-contrast text, and instructional pacing |
| `creator_ticker` | Pill-shaped creator ticker with active word color chips |

**Caption animation types (`CaptionAnimation`):** `fade_up`, `pop`, `type_on`, `glow`, `slide_up`, `impact_stamp`, `minimal_fade`, `edu_step`, and `ticker`.

Mode-aware implementation details:
- `CaptionStyle` now carries `mode`, background/card styling, border radius, padding, and letter spacing.
- `CAPTION_SCHEMA_VERSION = 2`; existing style JSON remains backward-compatible through Pydantic defaults.
- Caption chunking is mode-aware: impact stamps stay short, minimal lines stay single-line, education cards use step-sized phrases, and creator ticker stays compact.
- Remotion `AnimatedCaptions` renders the four mode layouts directly with per-segment entrance timing.
- FFmpeg `.ass` fallback receives matching static style metadata, including background boxes and educational step labels.
- The web editor exposes all Stage 5 presets, mode/animation controls, card/ticker styling, and preview rendering.

| Caption Mode | Target Style Classes | Look |
|---|---|---|
| `impact_stamp` | dramatic, motivational | Full-width, uppercase, film-style stamp with shake |
| `minimal_line` | luxury, cinematic | Lowercase, thin font, long fade, bottom-third |
| `educational_card` | educational | Rounded label, icon prefix, step indicator |
| `creator_ticker` | creator/influencer | Colored word highlight, emoji-capable, casual |

Caption highlight system (emphasis on high-emotion words) is already shipped in the `EditPlan` via `caption_highlights[]` with effects: `pulse`, `underline`, `color_pop`, `scale`, `none`.

### Stage 6 — Motion Design

**Current state:** Zoom punch and basic motion effects shipped via Remotion and effects config.

The `EffectsConfig` (`effects_config` table + `MotionEffectKey` enum) drives the Remotion `VisualGrade` layer:
- `zoom_punch` — scale transform on `zoom_moments` timestamps from the EditPlan
- `speed_ramp`, `motion_blur`, `flash`, `glitch`, `broll_slide_in`, `gradient_ramp` — all available as toggleable motion effects
- `gradient_ramp` with configurable `angle_deg`, `start_color`, `end_color`, `blend_mode`

The FFmpeg render path includes framing config support (`framing` param in `FFmpegCaptionRenderer`) for crop + reframe ops.

**Next version target (Phase 3):**
- Full per-clip motion keyframe editor in the UI
- Smooth push-in (Ken Burns) as a distinct preset
- Speed ramp in/out with easing curves
- Shake/impact on beat hits
- Vertical crop + pan auto-reframe for 9:16 conversion

All motion is non-destructive — applied at composition layer, not re-encoded into source.

### Stage 7 — Color Grading

**Current state:** Six visual grade presets shipped via Remotion CSS filters.

`VisualPresetKey` currently supports: `vogue_luxury`, `champagne_gold`, `dubai_influencer`, `old_money`, `clean_founder`, `cinematic_hotel`. Each preset maps to a `presetCssFilter()` that applies `contrast`, `saturate`, `sepia`, `brightness` adjustments.

Additional texture controls in `EffectsConfig`:
- `vignette` (0–1), `grain` (0–1), `light_leak` (0–1), `anamorphic_flare` (bool)
- `lut_intensity` (0–1) — controls grade intensity uniformly
- `gradient_ramp` — composited color overlay with blend mode

**Grade preset → style class mapping (target):**

| Style Class | Grade | Current Status |
|---|---|---|
| `luxury` / `old_money` | Muted saturation, high-end contrast, bleach bypass | `old_money`, `champagne_gold` presets ✅ |
| `cinematic` | Teal shadows, orange highlights, film grain | `cinematic_hotel` preset ✅ |
| `founder` / `minimal` | Neutral, clean, no stylization | `clean_founder` preset ✅ |
| `fashion` / `editorial` | High contrast, vogue look | `vogue_luxury` preset ✅ |
| `motivational` | Warm lift, bright mids, punchy contrast | Not yet separated from `champagne_gold` |
| `educational` | Natural, daylight-balanced | Not yet a distinct preset |
| `dramatic` | Crushed blacks, high contrast | Covered partially by `vogue_luxury` |
| `travel` / `fast-paced` | Punchy saturation, bright whites | Covered partially by `dubai_influencer` |
| `emotional` | Desaturated, cool shadows, warm highlights | Not yet implemented |
| `creator` | Bright and warm, pop saturation | `dubai_influencer` is closest |

**Next version target:** Full LUT pipeline via FFmpeg for export-path grading consistency. Per-scene grade normalization for exposure consistency across clip variations.

### Stage 8 — Music & Sound Design

**Current state:** Shipped. Licensed catalog provider + beat-aware audio mixing.

Music selection runs as a separate queue job (`MusicQueue`). The `LicensedCatalogMusicProvider` queries the `licensed_music_catalog` Supabase table (added in migration `202606110014`). Music metadata includes `beat_map` (array of beat timestamps in ms) and `sfx_urls` for SFX cue placement.

In the Remotion renderer (`MusicAndSfxMix`):
- Music volume: configurable, default 0.25
- Speech ducking: sidechain logic via `volumeWithSpeechDucking()` — attenuates music when captions are active (configurable dB, default −12 dB)
- SFX cue placement: beat-synced from `beat_map`, quantity controlled by `sfx_intensity` (0–1)
- `sfx_intensity` caps SFX count at `round(intensity * 6)` cues per render

In the FFmpeg renderer:
- Music mixing via `_build_audio_filter_graph()` — ducking, SFX at timestamp positions, loudness normalization target
- Up to 6 SFX sources composited with configurable intensity

**Next version target:**
- Source audio stem separation (Demucs/Spleeter) for videos with embedded music — clean speech/music split for ducking
- Music style classifier that matches mood to `music_mood` field from EditPlan
- BPM-sync of cut timing to music beat grid

### Stage 9 — Edit Assembly

**Current state:** Shipped. Two render paths available.

**FFmpeg path (standard):** `ffmpeg_render.py` — builds a full filter graph from the EditPlan manifest. Handles:
- Cut segments (`keep_segments[]` → `concat` filter)
- B-roll compositing (overlay at timed positions)
- ASS caption overlay (`ass_captions.py` generates `.ass` subtitle file)
- Effects config (crop, motion blur, color filter application)
- Music + SFX audio mix with ducking
- Output: H.264/AAC MP4

**Remotion path (premium):** `apps/renderer` — React/Remotion composition. Handles:
- `MotionlyPremiumComposition` root — `VideoScene` + `AnimatedCaptions` + `MusicAndSfxMix` + `VisualGrade`
- Template-driven layout via `registry.ts` (7 templates: `luxury_reel_v1`, `founder_reel_v1`, `travel_reel_v1`, `podcast_reel_v1`, `fashion_reel_v1`, `talking_head_reel_v1`, `product_promo_reel_v1`)
- Enabled via `REMOTION_ENABLED=true` in worker env
- Renders via `apps/worker/src/remotion.mjs` → Puppeteer/Chromium headless

The render pipeline is dispatched via `RenderQueue` (BullMQ). `render_jobs` table tracks: `queued → preparing → rendering → uploading → complete / failed`. Export files land in `EXPORT_STORAGE_BUCKET`.

---

## 4. Edit Manifest Schema

The Edit Manifest is the single source of truth for the entire project. It is versioned, diffable, and fully human-readable.

### Current EditPlan Schema (`EditPlan` Pydantic model, `schema_version: 1`)

```json
{
  "schema_version": 1,
  "style": "luxury | founder | travel | fashion | podcast",
  "target_duration_s": 35,
  "cuts": [
    { "start_ms": 0, "end_ms": 4200, "reason": "strong_story" }
  ],
  "caption_highlights": [
    { "timestamp_ms": 1200, "word": "success", "effect": "pulse | underline | color_pop | scale | none" }
  ],
  "broll_requests": [
    { "start_ms": 600, "end_ms": 4200, "keywords": ["luxury brand moment"], "priority": 0.9 }
  ],
  "zoom_moments": [
    { "timestamp_ms": 1200, "intensity": 1.12, "reason": "Emphasize keyword at 1.2s" }
  ],
  "music_mood": "uplifting ambient luxury",
  "effects": ["warm_grade", "soft_flash"],
  "color_grade": "champagne_gold",
  "risk_flags": ["aggressive_trim_may_remove_context"]
}
```

### Supporting Supabase Tables (shipped)

| Table | Purpose |
|---|---|
| `profiles`, `workspaces`, `workspace_members` | Identity + multi-workspace auth |
| `projects` | Top-level project record (status, title, active_plan_id) |
| `uploads` | Video upload state machine + metadata (duration, resolution, codec) |
| `transcripts` | Word-level transcript records linked to uploads |
| `edit_plans` | EditPlan JSON + provider/cost metadata, cache key, version |
| `broll_slots`, `stock_assets` | B-roll slot assignments + cached stock video records |
| `music_selections` | Selected music track + volume/sfx_intensity/mix config |
| `effects_configs` | VisualPresetKey + motion effects + texture settings |
| `render_jobs`, `exports` | Render job state machine + signed export URLs |
| `subscriptions`, `usage_ledger` | Stripe billing + render credit tracking |
| `licensed_music_catalog` | Licensed tracks with beat_map, sfx_urls, metadata |

---

## 5. Frontend Architecture

### Tech Stack (Shipped)

| Layer | Choice |
|---|---|
| Framework | Next.js (App Router), TypeScript |
| Styling | Tailwind CSS |
| Auth | Supabase Auth (browser + server client) |
| State | Local React state + server-side refreshes (no Zustand yet) |
| Video Preview | `<video>` element sync via `usePlaybackSync` hook |
| Timeline UI | Custom DOM-based timeline (`TimelinePanel.tsx`) |
| Real-time | Page router refresh polling (WebSocket planned) |
| Drag & Drop | dnd-kit palette → canvas/timeline/clip with snapping and accessible drag overlays |
| Error tracking | GlitchTip/Sentry (`instrumentation.ts`, `instrumentation-client.ts`) |

### Page Structure (Shipped)

```
/                      → Landing page
/login, /signup        → Supabase Auth
/(app)/dashboard       → Project library
/(app)/projects/new    → New project + upload flow
/(app)/projects/[id]   → Main editor
/(app)/billing         → Subscription + plan management
/(app)/account         → Profile settings
/(app)/admin/ops       → Internal admin ops panel
/pricing               → Pricing page
/privacy, /terms, /refund, /copyright → Legal pages
/feedback              → Feedback form
```

### Editor Layout (Current)

The current editor (`EditorClient.tsx`) uses a searchable dnd-kit element palette on the left and a contextual properties/tabbed inspector on the right. The WebGL-backed center preview composites manifest elements locally, and `TimelinePanel` exposes video, B-roll, caption, transition, FX, music, and SFX drop tracks.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  MOTIONLY  [Project Name]                               [← Back to Projects] │
├────────────────────────────────────────────────┬─────────────────────────────┤
│                                                │  [AI] [Cap] [B-roll] [Audio]│
│         VIDEO PREVIEW                          │  [Look] [Export]            │
│         (<video> element, 9:16 frame)          │                             │
│                                                │  Inspector panel content    │
│         ▶  0:04 / 0:32                         │  (tab-specific controls)    │
│                                                │                             │
├────────────────────────────────────────────────┴─────────────────────────────┤
│  TIMELINE                                                                     │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ playhead                │
│  [main]  [■■■■■■■][■■■■■][■■■■■■■■■■][■■■■■]                                │
│  [broll]       [═══════]        [═════════]                                  │
│  [caps]   W  O  R  D  B  Y  W  O  R  D                                       │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Target layout (Phase 3 — full editor):**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  MOTIONLY  [Project Name]                        [Export ▸]  [Save]  [Share] │
├────────┬─────────────────────────────────────────┬────────────────────────── ┤
│ELEMENT │                                         │                           │
│PALETTE │         VIDEO PREVIEW                   │   PROPERTIES INSPECTOR    │
│        │         (WebGL / 9:16 or 16:9)          │                           │
│📝 Text │                                         │  [Selected Element]       │
│🎬 Trans│                                         │  ┌─────────────────────┐  │
│🎨 FX   │                                         │  │ Position   Timing   │  │
│📹 Media│         ▶  0:04 / 0:32                  │  │ Style      Animate  │  │
│🎵 Audio│                                         │  └─────────────────────┘  │
│🔲 Shape│                                         │  [AI reason: "keyword     │
│🎭 Motio│                                         │   'success' at 1.2s"]     │
│🎨 Grade│                                         │                           │
│🤖 AI   │                                         │  [Replace]  [Delete]      │
├────────┴─────────────────────────────────────────┴───────────────────────────┤
│  TIMELINE                                                                     │
│  [main]  [■■■■■■■][■■■■■][■■■■■■■■■■][■■■■■]                                │
│  [broll]       [═══════]        [═════════]        ← drag B-roll here        │
│  [caps]   W  O  R  D  B  Y  W  O  R  D            ← drag caption here       │
│  [trans]       |╳|             |╳|                ← drag transition here     │
│  [music]  ≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋    ← drag track here        │
│  [sfx]                ◆         ◆                  ← drag SFX here           │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Live Preview System (Target)

Current: `<video>` decoding with `usePlaybackSync` feeds a WebGL2 compositing canvas; manifest-driven captions, placed elements, grades, motion, and drop previews redraw locally while timeline scrubbing updates `currentTime` directly.

Target (Phase 3):
- WebGL canvas compositing for caption + effects preview in real-time
- Manifest-driven redraw: any property change triggers a diff-based re-render of the current frame
- For full playback: WebCodecs-decode proxy frames → composite with canvas overlays at 30 fps
- No server round-trip for manual edits — all rendering is client-side on the proxy
- Server re-render only needed for final export at source resolution

---

## 6. Backend Architecture

### Tech Stack (Shipped)

| Layer | Choice |
|---|---|
| Runtime | Python 3.12 + FastAPI, uvicorn |
| Task Queue | BullMQ (Node.js bridge `queue-bridge.mjs`) + Redis |
| AI Transcription | Groq-compatible Whisper HTTP endpoint |
| AI Edit Planner | OpenRouter (or any OpenAI-compatible LLM endpoint) |
| Video Processing | FFmpeg (required in production) + media probe |
| Storage | Supabase Storage (signed URLs) or local filesystem |
| Database | Supabase / PostgreSQL + RLS policies |
| Cache | Redis (BullMQ only; manifest-level caching via DB cache key) |
| Container | Dockerfile for `apps/api` and `apps/worker`; Docker Compose for local |
| Billing | Stripe (subscriptions + webhooks) |

### Worker Topology (Shipped)

```
┌──────────────────────────────────────────────────────────┐
│   API Server (FastAPI)                                   │
│   /uploads  /projects  /plans  /broll  /music           │
│   /effects  /renders  /billing  /regeneration           │
└──────────────────────────┬───────────────────────────────┘
                           │ enqueue (BullMQ via Node bridge)
                    ┌──────▼──────┐
                    │  Redis Queue │
                    └──────┬──────┘
          ┌───────┬─────────┼─────────┬──────────┐
          ▼       ▼         ▼         ▼          ▼
    ┌──────────┐ ┌────────┐ ┌───────┐ ┌───────┐ ┌──────────┐
    │Preprocess│ │Transcr.│ │ Plan  │ │ Broll │ │  Render  │
    │  Queue   │ │ Queue  │ │ Queue │ │ Queue │ │  Queue   │
    └──────────┘ └────────┘ └───────┘ └───────┘ └──────────┘
                                                  ┌──────────┐
                                                  │  Music   │
                                                  │  Queue   │
                                                  └──────────┘
                                                  ┌──────────┐
                                                  │ Effects  │
                                                  │  Queue   │
                                                  └──────────┘
                                                  ┌──────────┐
                                                  │Regen.    │
                                                  │  Queue   │
                                                  └──────────┘
```

Each queue name maps to a BullMQ queue processed by `apps/worker/src/worker.mjs`. The worker calls `POST /internal/jobs/:id/run` back to the API for the actual business logic, keeping AI/FFmpeg logic in Python. Remotion renders run via `apps/worker/src/remotion.mjs` when `REMOTION_ENABLED=true`.

### API Endpoints (Shipped)

```
# Projects
POST   /projects                      → create project
GET    /projects/:id                  → project detail
PATCH  /projects/:id                  → update title / active plan

# Uploads
POST   /uploads                       → create upload + sign storage URL
GET    /uploads/:id                   → upload status + metadata
POST   /uploads/:id/complete          → mark upload complete, trigger preprocess

# Transcription
POST   /uploads/:id/transcripts       → queue transcription job
GET    /uploads/:id/transcripts/latest → word-level transcript

# Edit Plans
POST   /uploads/:id/plans             → queue AI edit planning
GET    /plans/:id                     → plan detail + EditPlan JSON

# B-roll
POST   /plans/:id/broll               → queue B-roll fetch
GET    /plans/:id/broll               → broll slots with stock asset URLs

# Music
POST   /plans/:id/music               → queue music selection
GET    /plans/:id/music               → music selection detail

# Effects
POST   /plans/:id/effects             → queue effects config generation
GET    /plans/:id/effects             → effects config detail
PATCH  /plans/:id/effects             → manual override effects config

# Captions
PATCH  /uploads/:id/captions/:trackId → update caption style / preset
POST   /uploads/:id/captions/regenerate → re-run caption generation

# Renders
POST   /renders                       → queue render job
GET    /renders/:id                   → render status + export URL

# Regeneration
POST   /plans/:id/regenerate          → partial re-run of specific layers

# Billing
GET    /billing/plans                 → available subscription plans
POST   /billing/checkout              → create Stripe checkout session
POST   /billing/portal                → create Stripe customer portal session
POST   /billing/webhook               → Stripe webhook handler

# Internal (worker-only)
POST   /internal/jobs/:id/run         → execute queued job
POST   /internal/renders/:id/run      → execute render job
```

---

## 7. Manual Edit Capabilities (Post-AI)

Every AI-generated decision is stored as an overridable property in the Edit Manifest or its associated tables. The manual editor exposes these without requiring knowledge of the underlying structure.

### Currently Editable (Shipped)

| User Action | Where Handled |
|---|---|
| Edit caption text | `CaptionsTab` → `PATCH /captions/:trackId` |
| Change caption preset | `CaptionsTab` → preset picker → API update |
| Adjust caption timing | `CaptionsTab` → segment timing controls |
| Toggle hide filler words | `CaptionsTab` → `hide_filler_words` flag |
| Change B-roll keyword search | `BrollTab` → replace B-roll by slot |
| Replace B-roll clip | `BrollTab` → stock video search + assign |
| Change color grade preset | `LookTab` → `VisualPresetKey` picker |
| Adjust effects config | `LookTab` → motion effects checkboxes + texture sliders |
| Change music track | `AudioTab` → music browser |
| Adjust music volume / SFX intensity | `AudioTab` → sliders |
| Trim clip in timeline | `TimelinePanel` → drag clip edge → `PATCH /plans/:id` |
| Nudge clip (± 500ms) | `TimelinePanel` → keyboard nudge |
| Re-run AI plan | `AIStep` → new prompt + style → `POST /plans` |
| Regenerate specific layers | `RegenerationTab` → `POST /plans/:id/regenerate` |

### Target Editable (Phase 3)

| User Action | Manifest Operation |
|---|---|
| Drag clip edge to trim | Update `cut.start_ms` / `cut.end_ms` |
| Reorder clips | Reorder cuts array, fix timestamps |
| Split clip | Duplicate cut entry, adjust timestamps |
| Delete segment | Remove cut, close gap or fill with B-roll |
| Drag transition handle | Update adjacent cut in/out symmetrically |
| Toggle motion effect per clip | Update `zoom_moments` / effects config |
| Adjust color manually | Write grade override per parameter |
| Undo/redo | Command pattern, 50-step history stack |

---

## 8. B-Roll Library System

### Current Structure (Shipped)

```
stock_assets {
  id: uuid
  workspace_id: uuid
  project_id: uuid
  provider: "pexels" | "pixabay" | "fixture"
  provider_asset_id: text
  title: text
  thumbnail_url: text
  preview_video_url: text
  download_url: text
  license: text
  width: integer
  height: integer
  duration_ms: integer
  orientation: "portrait" | "landscape" | "square"
  raw_metadata: jsonb
}

broll_slots {
  id: uuid
  plan_id: uuid
  slot_index: integer
  start_ms: integer
  end_ms: integer
  keywords: text[]
  priority: float
  status: "ready" | "disabled" | "failed"
  stock_asset_id: uuid (nullable)
  override_asset_id: uuid (nullable)   ← user replacement
}
```

### Current Search Strategy

1. B-roll keywords come from the LLM edit planner's `broll_requests[].keywords`
2. `BrollQueue` job calls `PexelsStockVideoProvider` / `PixabayStockVideoProvider` with keyword text search
3. Results are stored in `stock_assets` and linked to `broll_slots`
4. User can replace any slot via the BrollTab in the editor

### Target Search Strategy (Phase 3)

1. Compute CLIP embedding for each keyword phrase from the transcript
2. Run cosine similarity search in pgvector against B-roll embeddings
3. Filter by duration compatibility, motion type, color temperature match
4. Return top 10 candidates ranked by semantic + visual quality score
5. User can accept AI suggestion or browse alternatives

---

## 9. Export System

### Current State (Shipped)

**FFmpeg basic path:** Available for all plans. Renders cut segments → B-roll overlay → ASS captions → audio mix → H.264/AAC MP4.

**Remotion premium path:** Available when `REMOTION_ENABLED=true`. Renders full React composition → higher-quality animated captions, visual grades, music sync.

**Render engine routing:** The `render_jobs` table stores `render_engine: "ffmpeg_basic" | "remotion_premium"`. The worker runs the appropriate path.

### Output Formats (Target)

| Platform | Aspect Ratio | Resolution | Codec | Target Loudness |
|---|---|---|---|---|
| TikTok / Reels / Shorts | 9:16 | 1080×1920 | H.264 | −14 LUFS |
| YouTube (landscape) | 16:9 | 1920×1080 | H.264 | −14 LUFS |
| YouTube Shorts | 9:16 | 1080×1920 | H.264 | −14 LUFS |
| Instagram Post | 4:5 | 1080×1350 | H.264 | −14 LUFS |
| Twitter/X | 16:9 | 1280×720 | H.264 | −14 LUFS |
| Custom | User-defined | Up to 4K | H.265 or ProRes | −14 LUFS |

`ExportResolution` type: `"720p" | "1080p" | "4k"`. `PlatformPreset` type: `"instagram" | "tiktok" | "shorts"`.

### Export Process (Current FFmpeg Implementation)

1. Validate EditPlan and render config
2. Download/resolve source video and B-roll assets from storage
3. Build `concat` filter graph for cut segments
4. Overlay B-roll clips at timed positions via `overlay` filter
5. Generate `.ass` subtitle file for captions via `ass_captions.py`
6. Apply CSS-equivalent color filter if not using Remotion
7. Mix audio: speech + music (ducked) + SFX with loudness normalization
8. Mux all streams into H.264/AAC MP4
9. Upload to `EXPORT_STORAGE_BUCKET`, generate signed download URL

---

## 10. Implementation Phases

### Phase 1 — Foundation ✅ Complete (Weeks 1–12 shipped)

- [x] Monorepo: Next.js + FastAPI + Node worker + Remotion renderer
- [x] Upload service: chunked upload, Supabase storage, FFmpeg preprocessing
- [x] Audio pipeline: Groq Whisper word-level transcription, language hint
- [x] Edit Manifest schema v1: EditPlan Pydantic model, Supabase tables
- [x] LLM edit planner: OpenRouter HTTP provider + fixture fallback + repair layer
- [x] Caption system: 9 presets, mode-aware segments, Remotion animated rendering
- [x] FFmpeg export pipeline: cuts + B-roll + ASS captions + audio mix
- [x] Remotion premium render path: 7 templates, visual grades, music/SFX mix
- [x] B-roll engine: Pexels + Pixabay providers, slot assignment
- [x] Music system: licensed catalog provider, beat-map aware, speech ducking
- [x] Effects config: 6 visual grade presets, 7 motion effect keys, texture controls
- [x] Regeneration API: partial layer re-run per `RegenerationLayer` enum
- [x] Billing: Stripe subscriptions (free / creator / pro / agency) + usage ledger
- [x] Auth: Supabase Auth (email/password), workspace multi-tenancy, RLS
- [x] Admin panel: ops page for internal monitoring
- [x] Error tracking: GlitchTip integration on web + API
- [x] CI/CD config: Codex config, Prettier, ESLint, TypeScript checks

**Current milestone:** Full upload → AI analysis → edit plan → render → export pipeline working end-to-end. Billing live. Auth multi-workspace live.

### Phase 2 — Editor Polish & Manual Editing (Current — Weeks 13–16)

The current editor has all AI pipeline controls but lacks the full manual editing experience. This phase completes the editing surface.

- [ ] Timeline drag-trim: drag clip edges to adjust `cut.start_ms` / `cut.end_ms`
- [ ] Timeline clip reorder, split, delete
- [ ] Undo/redo system (command pattern, 50-step history)
- [ ] Keyboard shortcuts: J/K/L playback, `[` / `]` trim, `Cmd+Z` undo
- [ ] B-roll slot manual drag: drag B-roll clip to a different timeline position
- [ ] Caption editor: inline text edit on selected segment
- [ ] Color grade live preview: hover grade preset → immediate preview frame
- [ ] Motion effect per-clip selector in inspector panel
- [ ] AI reasoning labels visible on selected elements ("AI reason: keyword at 1.2s")
- [ ] Confidence scores visible in inspector panel
- [ ] Project autosave indicator

**Milestone:** Any AI decision is overridable. Timeline feel is fluid. Inspector panel shows AI reasoning.

### Phase 3 — Full Manual Editor + Drag-and-Drop System (Weeks 17–24)

- [x] WebGL preview canvas — manifest-driven, zero-latency frame redraws
- [x] Element Palette sidebar (left): all categories, searchable, thumbnails
- [x] Drag-to-Canvas: text elements, overlays, shapes, graphics
- [x] Drag-to-Timeline: B-roll, transitions, SFX, music, captions (dnd-kit)
- [x] Drag-to-Clip: color grades, motion effects
- [x] Ghost/shadow preview while dragging
- [x] Snap system: grid snap on canvas, boundary snap on timeline
- [x] Drop zone highlight: canvas grid + timeline insert indicator
- [x] Transition drop zones between clips
- [x] Properties Inspector: live editing panel for every element type
- [x] Element palette search and filter
- [x] Palette thumbnail previews for grades, caption styles, transitions
- [x] AI Elements in palette: Auto-Caption Clip, Smart B-roll Fill, AI Music Match, Re-analyze Style, AI Highlight Reel
- [x] Visual intelligence pipeline: CLIP embeddings, BLIP-2 captions, scene classification
- [x] CLIP-based B-roll semantic search (replace keyword text search)
- [x] User B-roll library upload + management
- [x] Speaker diarization (pyannote.audio) for podcast/interview content
- [x] Additional style classes: `motivational`, `emotional`, `cinematic`, `educational`, `dramatic`, `fast-paced`, `clean minimal`, `creator/influencer`
- [x] Remaining caption modes: `impact_stamp`, `minimal_line`, `educational_card`, `creator_ticker`
- [x] Full LUT-based color grading in export path

**Milestone:** Full manual editing capability. Every AI decision overridable. Spatial element palette. Live WebGL preview. Expanded style system.

### Phase 4 — Export & Platform (Weeks 25–28)

- [x] GPU-accelerated FFmpeg export (A10/T4 worker tier)
- [x] Multi-format export presets (TikTok, Reels, Shorts, YT landscape)
- [x] Export progress UI with real-time ETA
- [x] Direct publish integrations (TikTok, Instagram, YouTube APIs)
- [x] Project version history (save named snapshots of EditPlan)
- [x] Bulk export: multiple format targets from one render job
- [x] Watermark removal for paid tiers

**Milestone:** Production-grade export. Full upload-to-publish flow.

### Phase 5 — Intelligence & Scale (Weeks 29–36)

- [ ] Emotion detection: wav2vec2-based per-500ms window (joy, sadness, intensity, urgency)
- [ ] Pacing analysis: BPM-equivalent speech rhythm + pause map
- [ ] Audio stem separation (Demucs) for source videos with embedded music
- [ ] AI-generated B-roll via Runway Gen-3 (premium tier)
- [ ] Custom style training (user uploads 5 reference videos → AI learns style)
- [ ] Batch processing (multiple videos, same style)
- [ ] Real-time collaboration (shared manifest, presence indicators)
- [ ] Template library (save AI edit settings as reusable templates)
- [ ] Analytics: which AI edits users keep vs. change
- [ ] Model fine-tuning loop: improve AI decisions based on user overrides

---

## 11. Key Technical Challenges

### Challenge 1 — Latency of AI Pipeline

**Problem:** Transcription + LLM edit planning + B-roll fetch + music selection + effects config running sequentially is slow for long videos.

**Current state:** Pipeline stages run as separate BullMQ queues with worker concurrency control (`WORKER_CONCURRENCY`). B-roll, music, and effects queues are independent after the plan job completes.

**Mitigations:**
- Audio and B-roll analysis run in parallel (separate queues) after plan is ready
- Groq Whisper endpoint is fast (~2–5s for 60s audio)
- LLM edit planner has `FixtureEditPlannerProvider` for dev/testing without API latency
- Plan caching via SHA-256 digest of transcript + request params — identical request hits cache
- Progressive client updates: each queue stage triggers a state update via API polling

**Target:** Complete AI pipeline in under 90 seconds for a 60-second video.

### Challenge 2 — Preview vs. Export Fidelity

**Problem:** The current `<video>` preview does not reflect caption animation, color grades, or B-roll compositing. Users see the raw source video until render completes.

**Current mitigation:** Remotion renders a preview-quality MP4 that includes full composition. After render, the `<video>` element shows the actual rendered output.

**Target mitigation (Phase 3):** WebGL canvas compositing mirrors the Remotion composition in real-time. CSS filter equivalents of grade presets map 1:1 to FFmpeg filter params. Caption animation state drives both canvas and Remotion.

### Challenge 3 — B-Roll Licensing

**Problem:** Pexels/Pixabay free tiers have rate limits and attribution requirements.

**Current state:** Both providers implemented. License field stored in `stock_assets`. Attribution surfaced to user before export.

**Mitigation roadmap:**
- Phase 3: User B-roll library upload (no licensing friction)
- Phase 4: Artlist/Storyblocks API partnerships
- Phase 5: AI-generated B-roll via Runway Gen-3 (premium tier)

### Challenge 4 — Caption Animation in Export

**Problem:** FFmpeg does not natively support word-by-word animated captions.

**Current solution:** Two-path render system — FFmpeg path uses `.ass` subtitle overlay (supported natively by FFmpeg `ass` filter) for standard quality. Remotion path renders full React caption animations to frames and composites via the worker. Premium users get Remotion path when `REMOTION_ENABLED=true`.

**Target:** Remotion becomes the default path as infrastructure scales. FFmpeg path remains for low-cost/fast exports.

### Challenge 5 — Style Classification Accuracy

**Problem:** Misclassifying a luxury brand video as `podcast` produces jarring results — wrong grade, wrong caption style, wrong B-roll keywords.

**Current mitigation:** LLM edit planner with structured JSON schema prompt considers transcript content + user prompt together. Style is shown immediately in the AI tab and trivially easy to change. Plan caching avoids re-running the LLM for the same input.

**Target mitigation (Phase 3):** Separate pre-classifier running on audio + visual signals before the LLM planner. Store user overrides and use them for fine-tuning loop (Phase 5).

### Challenge 6 — Fixture Provider Drift

**Problem:** `FixtureEditPlannerProvider` and `FixtureMusicProvider` produce deterministic outputs used in dev and testing. If the real providers change behavior, fixtures can mask integration regressions.

**Mitigation:** `ALLOW_FIXTURE_PROVIDERS=false` is enforced in production via `_validate_production_settings()`. Fixture providers are completely blocked in production. Test suite covers both fixture and real-provider paths.

---

## 12. Success Metrics

| Metric | Target (Phase 4 launch) |
|---|---|
| Time from upload to first AI edit | < 90 seconds for 60s video |
| % of AI edits where user makes < 3 manual changes | > 60% |
| Export success rate | > 99.5% |
| Average export time (60s video, 1080p) | < 3 minutes |
| User satisfaction with AI caption style | > 4.2 / 5 |
| B-roll relevance score (user-rated) | > 3.8 / 5 |
| AI style classification accuracy | > 82% first-pass match |
| Pipeline stage P95 latency (transcription) | < 10s for 60s audio |
| Pipeline stage P95 latency (edit planning) | < 15s |
| Render job P95 completion time (60s, 1080p) | < 3 minutes |

---

## 13. Open Questions

1. **AI-generated B-roll:** At what quality bar does generated B-roll feel premium vs. uncanny? Needs user testing before production.
2. **Vertical vs. Landscape:** Should the AI automatically reframe 16:9 footage to 9:16 for short-form? How to handle complex scenes with multiple subjects?
3. **User audio:** If the user uploads footage with background music already embedded, how do we cleanly separate stems for ducking? (Demucs / Spleeter — evaluate quality tradeoff.) Currently the pipeline processes the full audio; stem separation is Phase 5.
4. **Multiple speakers:** Diarization-aware caption positioning for interview/podcast-style content — show speaker labels? Planned via pyannote.audio in Phase 3.
5. **Long-form input:** Pipeline is optimized for short-form output. For a 10-minute input file, should the AI produce a 60-second highlight reel, or edit the full length? The `RegenerationLayer.cuts` path supports AI re-trim; full-length editing needs explicit product decision.
6. **Copyright of source video:** Does Motionly need content moderation before pipeline runs? (NSFW detection, watermark detection.) Not currently implemented.
7. **Offline/on-device:** Is there a future path for running a lightweight version of the pipeline locally via WASM/WebGPU for privacy-sensitive users?
8. **EditPlan schema versioning:** `PLANNER_SCHEMA_HASH` in `edit_planner.py` invalidates cache on schema changes. As the EditPlan model evolves (new fields, new style classes), old cached plans need migration or invalidation. Needs a forward-compat strategy before Phase 3 style expansion.

---

## 14. Dependencies & External Services

| Service | Purpose | Status |
|---|---|---|
| Groq API (`groq_api_key`) | Whisper-compatible transcription endpoint | ✅ Integrated |
| OpenRouter (`openrouter_api_key`) | LLM edit planner (any OpenAI-compat model) | ✅ Integrated |
| Pexels API (`pexels_api_key`) | B-roll stock video search | ✅ Integrated |
| Pixabay API (`pixabay_api_key`) | B-roll stock video search | ✅ Integrated |
| Supabase | Auth, database, storage, RLS | ✅ Core |
| Stripe | Billing, subscriptions, webhooks | ✅ Integrated |
| FFmpeg | Video processing & export | ✅ Core |
| Remotion | Premium caption render layer | ✅ Integrated (optional) |
| Redis / BullMQ | Job queue | ✅ Core |
| GlitchTip | Error tracking (Sentry-compatible) | ✅ Integrated |
| Runway Gen-3 API | AI-generated B-roll | Phase 5 |
| pyannote.audio | Speaker diarization | ✅ Dependency + worker provider integrated |
| wav2vec2 / YAMNet | Emotion and background sound analysis | ✅ Dependency + worker providers integrated |
| CLIP / BLIP-2 / YOLO / MediaPipe | Visual intelligence embeddings, captions, objects, faces | ✅ Integrated |
| Demucs / Spleeter | Audio stem separation | Phase 5 |
| Artlist / Storyblocks | Licensed music partnerships | Phase 4 |

---

## 15. Drag-and-Drop Element System

This is the core of Motionly's manual editing experience for Phase 3. Inspired by Android Studio's Layout Editor — where you drag UI components from a palette onto a canvas and configure them in a properties panel — Motionly's drag-and-drop system gives users the same spatial, visual-first building experience for video.

The principle: **any visual or audio element you can see in a final video can be dragged from the palette and dropped exactly where you want it** — either positioned on the canvas (spatial) or placed on the timeline (temporal).

---

### 15.1 The Element Palette (Left Sidebar)

The palette is a categorized, scrollable library of all elements available to place in the project. Each category is collapsible. Elements are shown as labeled tiles with thumbnail previews.

```
┌─────────────────────────┐
│  🔍 Search elements...  │
├─────────────────────────┤
│ ▾ 📝 TEXT & CAPTIONS    │
│   [Word Pop]  [Phrase]  │
│   [Impact]    [Minimal] │
│   [Edu Card]  [Ticker]  │
│   [Title Card][Lower 3rd│
│   [Custom Text]         │
├─────────────────────────┤
│ ▾ 🎬 TRANSITIONS        │
│   [Hard Cut] [Dissolve] │
│   [Zoom In]  [Zoom Out] │
│   [Swipe L]  [Swipe R]  │
│   [Glitch]   [Whip Pan] │
│   [Fade Blk] [Light Lk] │
├─────────────────────────┤
│ ▾ 🎨 EFFECTS & OVERLAYS │
│   [Film Grain][Vignette]│
│   [Lens Flare][Bokeh]   │
│   [Light Leak][Halation]│
│   [Smoke]    [Sparkle]  │
│   [Emoji]    [Sticker]  │
├─────────────────────────┤
│ ▾ 📹 MEDIA              │
│   [B-roll Search]       │
│   [My Library]          │
│   [Solid Color]         │
│   [Gradient Fill]       │
│   [Screen Record]       │
├─────────────────────────┤
│ ▾ 🎵 AUDIO              │
│   [Music Track]         │
│   [SFX: Impact]         │
│   [SFX: Whoosh]         │
│   [SFX: Notification]   │
│   [Ambient Sound]       │
│   [Voice-over]          │
├─────────────────────────┤
│ ▾ 🔲 SHAPES & GRAPHICS  │
│   [Rectangle][Circle]   │
│   [Arrow]    [Line]     │
│   [Progress Bar]        │
│   [Countdown Timer]     │
│   [Stats Card]          │
│   [Quote Card]          │
│   [CTA Button]          │
│   [Social Handle Badge] │
├─────────────────────────┤
│ ▾ 🎭 MOTION EFFECTS     │
│   [Push-In]  [Punch Zm] │
│   [Ken Burns][Speed Rmp]│
│   [Shake]    [Reframe]  │
│   [Slow Mo]  [Reverse]  │
├─────────────────────────┤
│ ▾ 🎨 COLOR GRADES       │
│   [Vogue Luxury  ████]  │
│   [Champagne Gold████]  │
│   [Dubai Influenc████]  │
│   [Old Money     ████]  │
│   [Clean Founder ████]  │
│   [Cinematic Hotel████] │
│   [+ All presets]       │
├─────────────────────────┤
│ ▾ 🤖 AI ELEMENTS        │
│   [Auto-Caption Clip]   │
│   [Smart B-roll Fill]   │
│   [AI Music Match]      │
│   [Re-analyze Style]    │
│   [AI Highlight Reel]   │
└─────────────────────────┘
```

---

### 15.2 Drop Zones

There are three distinct drop zones, each accepting specific element categories:

**Drop Zone A — Canvas (spatial positioning)**
The video preview canvas. Elements dropped here are visually positioned on the frame and persist across the full timeline range or a user-defined range.

**Drop Zone B — Timeline track (temporal placement)**
Each timeline track is a horizontal drop zone. Dropping places the element at the timestamp corresponding to the drop position.

**Drop Zone C — Per-clip (applied to a single clip)**
Dropping directly onto an existing clip in the timeline applies the element only to that clip.

---

### 15.3 Drop Zone Behavior by Element

| Element Type | Canvas | Timeline Track | Per-Clip |
|---|---|---|---|
| Text & Captions | ✅ position on frame | ✅ captions track | — |
| Transitions | ❌ | ✅ between clips only | — |
| Effects & Overlays | ✅ visual FX, stickers | ✅ FX track | ✅ on clip |
| Media (B-roll) | ❌ | ✅ B-roll track | — |
| Audio (Music) | ❌ | ✅ music track | — |
| Audio (SFX) | ❌ | ✅ SFX track at point | — |
| Shapes & Graphics | ✅ position on frame | ✅ graphics track | — |
| Motion Effects | ❌ | — | ✅ on clip only |
| Color Grades | ❌ | — | ✅ one clip or all |
| AI Elements | — | ✅ runs AI on drop | ✅ runs AI on clip |

---

### 15.4 Properties Inspector

When an element is selected (clicked or after drop), the Properties Inspector (right panel) shows contextual controls.

#### Caption / Text Inspector
```
┌──────────────────────────────┐
│  📝 Word Pop Caption         │
│  ─────────────────────────── │
│  ▾ CONTENT                   │
│    Text: [success          ] │
│    [Edit in context]         │
│                              │
│  ▾ TIMING                    │
│    Start: [1.24s] End:[1.68s]│
│    ○ Lock to word timing     │
│                              │
│  ▾ STYLE                     │
│    Color: [████ #FFD700]     │
│    Shadow: [ON]  Blur: [4px] │
│    Stroke: [OFF]             │
│                              │
│  ▾ ANIMATION                 │
│    In:  [Scale Bounce  ▼]    │
│    Out: [Fade          ▼]    │
│    Emphasis: [Color Pulse ▼] │
│                              │
│  AI Reason: "Keyword at 1.2s"│
│  Confidence: ████░ 91%       │
└──────────────────────────────┘
```

#### Clip / B-Roll Inspector
```
┌──────────────────────────────┐
│  📹 B-Roll Clip              │
│  ─────────────────────────── │
│  ▾ SOURCE                    │
│    [Thumbnail preview]       │
│    Pexels · ID: 1234567      │
│    [Replace B-roll...]       │
│                              │
│  ▾ TIMING                    │
│    In:  [0.00s]  Out:[3.40s] │
│    Duration: 3.40s           │
│    Speed: [1.0×          ▼]  │
│                              │
│  ▾ POSITION (on canvas)      │
│    Scale: [100%]             │
│    X: [0%]   Y: [0%]         │
│                              │
│  ▾ MOTION                    │
│    Type: [Push-In       ▼]   │
│    Intensity: ──●────── 40%  │
│    Easing: [Ease Out Cubic▼] │
│                              │
│  AI Reason: "Keyword: 'work'"│
│  Confidence: ███░░ 82%       │
└──────────────────────────────┘
```

#### Color Grade Inspector
```
┌──────────────────────────────┐
│  🎨 Champagne Gold           │
│  ─────────────────────────── │
│  ▾ PRESET                    │
│    [Champagne Gold     ▼]    │
│    [Preview thumbnail]       │
│                              │
│  ▾ ADJUSTMENTS               │
│    LUT Intensity: ──●──── 0.8│
│    Vignette:   ─●───── 0.3   │
│    Grain:      ─●───── 0.2   │
│    Light Leak: ─●───── 0.1   │
│                              │
│  ▾ GRADIENT RAMP             │
│    [ON]  Angle: [135°]       │
│    Start: [████ #FFD700]     │
│    End:   [████ transparent] │
│    Blend: [screen       ▼]   │
│                              │
│  Applied to: All clips       │
│  ○ This clip only            │
└──────────────────────────────┘
```

---

### 15.5 AI Elements — Special Drop Behavior

AI element drops trigger AI pipeline sub-tasks rather than adding a static element.

| AI Element | Drop Behavior |
|---|---|
| **Auto-Caption Clip** | Drop on clip → runs Whisper on that clip segment → adds word-level captions to captions track |
| **Smart B-roll Fill** | Drop on main-track segment → AI analyzes range and fills B-roll automatically |
| **AI Music Match** | Drop on timeline → AI selects music track matching current video mood (`music_mood` from EditPlan) |
| **Re-analyze Style** | Drop anywhere → re-runs style classifier → updates grade + caption style + motion globally |
| **AI Highlight Reel** | Drop on timeline → AI trims full video to best 30–60 second version |

---

### 15.6 Keyboard + Drag Modifiers

| Modifier | Effect |
|---|---|
| `Alt` + drop on clip | Apply effect to all clips in timeline |
| `Shift` + drop | Fine snap (0.1s resolution on timeline) |
| `Cmd/Ctrl` + drag | Duplicate an existing element instead of moving it |
| `Escape` during drag | Cancel drag, return element to palette |
| `Cmd/Ctrl` + `Z` | Undo last drop |
| `Space` | Play/pause preview during drag session |
| `Delete` | Remove selected placed element |

---

### 15.7 Implementation Notes

**Library:** dnd-kit (`@dnd-kit/core` + `@dnd-kit/sortable`)
- Accessible (ARIA live announcements for drag actions)
- Touch-compatible (mobile editing support)
- No native HTML5 drag API quirks
- Custom collision detection strategies for the timeline (closest edge vs. point)

**Key implementation decisions:**
- Timeline uses custom collision detection based on timestamp position, not DOM proximity
- Canvas uses pointer event coordinates mapped to video percentage coordinates
- Ghost preview for transitions renders a real WebGL composite frame at 50% opacity
- Color grade preview during hover uses a CSS filter uniform swap — zero latency for the current CSS-based grade system
- Element drops are immediately written to the Edit Manifest tables and trigger a diff-based re-render
- All drop operations go through the existing `PATCH /plans/:id` or layer-specific endpoints; no new endpoints needed for Phase 3 drag-drop

**State model for drag sessions:**
```typescript
type DragSession = {
  elementType: ElementCategory;
  elementId: string;
  sourcePosition: "palette" | "timeline" | "canvas";
  currentDropZone: "canvas" | "timeline" | "clip" | "none";
  snapTarget: SnapTarget | null;
  ghostPosition: { x: number; y: number };
  previewState: PreviewOverride | null; // for live grade/transition preview
};
```

---

*Document version: 2.0 | Last updated: June 2026 | Status: Phase 3 complete*
