# Data handling

This document describes how Motionly shares data with external providers and processes data in internal storage. Provider contracts, retention settings, and user consent controls remain authoritative over this summary.

## External AI and ML providers

1. **Groq**
   - **Service:** Audio transcription.
   - **Data shared:** Uploaded audio files are sent to the Groq-compatible transcription endpoint for speech-to-text.
2. **OpenRouter**
   - **Service:** Edit-plan generation.
   - **Data shared:** Transcripts and prompts are sent to OpenRouter chat completions to construct edit plans.
3. **Runway**
   - **Service:** Video generation.
   - **Data shared:** Prompt details and generation requests are sent to the Runway API.

## External media providers

1. **Pexels and Stock Providers**
   - **Service:** Stock-video search and retrieval.
   - **Data shared:** Keywords and search queries are sent to approved stock providers to fetch relevant B-roll clips.

## Internal Storage

Media files, thumbnails, and exports are stored in approved Supabase Storage buckets or local storage when the deployment explicitly enables local storage. Access, retention, deletion, and tenant isolation controls are described in [Data retention](../data-retention.md), [Edit-plan integrity](edit-plan-integrity.md), and [Phase 0 tenancy matrix](phase-0-tenancy-matrix.md).

## Review requirements

Provider changes must update this document, the relevant privacy/legal review evidence, and the provider contract tests before release. Do not place credentials, raw user media, transcripts, or provider responses in this repository.
