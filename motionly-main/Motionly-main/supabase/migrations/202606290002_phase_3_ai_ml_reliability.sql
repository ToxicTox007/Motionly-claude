-- Phase 3 AI/ML reliability metadata.

alter table public.transcripts
  add column if not exists quality_status text not null default 'ok',
  add column if not exists quality_reasons jsonb not null default '[]'::jsonb;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'transcripts_quality_status_known'
  ) then
    alter table public.transcripts
      add constraint transcripts_quality_status_known check (
        quality_status in ('ok', 'low_confidence', 'empty', 'audio_failed', 'partial')
      );
  end if;
end $$;
