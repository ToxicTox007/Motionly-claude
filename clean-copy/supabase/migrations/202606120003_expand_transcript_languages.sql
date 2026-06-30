-- Allow production transcription/caption language choices beyond the initial EN/HI beta.

alter table public.transcripts
  drop constraint if exists transcripts_language_check;

alter table public.transcripts
  add constraint transcripts_language_check
  check (language in ('en', 'hi', 'es', 'pt', 'fr', 'de', 'ar', 'unknown'));

alter table public.transcripts
  drop constraint if exists transcripts_language_hint_check;

alter table public.transcripts
  add constraint transcripts_language_hint_check
  check (language_hint in ('auto', 'en', 'hi', 'es', 'pt', 'fr', 'de', 'ar'));
