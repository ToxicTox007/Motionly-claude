# Data handling

This document describes how Motionly shares data with external providers and processes data in internal storage. Provider contracts, retention settings, and user consent controls remain authoritative over this summary.

## External AI and ML providers

Motionly does not hard-code its inference providers. The operator configures every AI capability in the inference registry (see the [registry guide](../ai-registry/README.md)), and each remote route names the data processor it sends data to in `data_processor` metadata: display name, purpose, data categories and policy URL.

- **Production rule:** a remote route without `data_processor` metadata is invalid in production and is dropped at startup (plan Section 2.6). Self-hosted routes (`local` adapters or `network: private`) are not external processors and are not listed.
- **Public list:** `GET /public/data-processors` returns one entry per distinct processor of the enabled remote routes, with display metadata only (no base URLs, model ids, route ids or secrets). The privacy page renders exactly this list.
- **What each capability shares:**

| Capability                                  | Data sent to the configured processor                                         |
| ------------------------------------------- | ----------------------------------------------------------------------------- |
| `planning.chat`                             | Transcripts and edit prompts                                                  |
| `transcription.speech_to_text`              | The uploaded audio track                                                      |
| `vision.object_detection`, `vision.caption` | Keyframe images                                                               |
| `embeddings.text`, `embeddings.image`       | B-roll keywords and keyframe images                                           |
| `audio.diarization`, `audio.emotion`        | The audio track, under a random name; no user, project or file name is shared |
| `audio.stem_separation`                     | An audio-only rendition; video never leaves the worker                        |
| `video.generation.submit`                   | The generation prompt and conditioning image                                  |

The registry examples under [`docs/ai-registry/examples/`](../ai-registry/examples/) show complete `data_processor` entries for the example providers.

## External media providers

1. **Pexels and Stock Providers**
   - **Service:** Stock-video search and retrieval.
   - **Data shared:** Keywords and search queries are sent to approved stock providers to fetch relevant B-roll clips.

## Internal Storage

Media files, thumbnails, and exports are stored in approved Supabase Storage buckets or local storage when the deployment explicitly enables local storage. Access, retention, deletion, and tenant isolation controls are described in [Data retention](../data-retention.md), [Edit-plan integrity](edit-plan-integrity.md), and [Phase 0 tenancy matrix](phase-0-tenancy-matrix.md).

## Review requirements

Adding or changing a remote route's processor must update its registry `data_processor` metadata, the relevant privacy/legal review evidence, and the provider contract tests before release. Do not place credentials, raw user media, transcripts, or provider responses in this repository.
