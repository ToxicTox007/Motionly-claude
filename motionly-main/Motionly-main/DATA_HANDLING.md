# Data Handling Documentation

This document describes how data is shared and processed with external providers and internal storage in the Motionly application.

## External AI and ML Providers

1. **Groq**
   - **Service**: Audio transcription.
   - **Data Shared**: Uploaded audio files are sent to the Groq compatible transcription endpoint for speech-to-text.

2. **OpenRouter**
   - **Service**: Edit plan generation.
   - **Data Shared**: Transcripts and prompts are sent to OpenRouter chat completions to construct edit plans.

3. **Runway**
   - **Service**: Video generation (Runway Gen3).
   - **Data Shared**: Prompt details and generation requests are sent to Runway API.

## External Media Providers

1. **Pexels and Stock Providers**
   - **Service**: Stock video search and retrieval.
   - **Data Shared**: Keywords and search queries are sent to Pexels and Pixabay to fetch relevant B-roll clips.

## Internal Storage

1. **Internal Storage**
   - **Service**: Storage buckets.
   - **Data Shared**: Media files, thumbnails, and exports are stored securely in Supabase storage buckets or local storage (if forced).
