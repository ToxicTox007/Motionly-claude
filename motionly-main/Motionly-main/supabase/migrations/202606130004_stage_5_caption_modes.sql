-- Stage 5 kinetic caption modes.
-- Extends the caption preset enum for impact stamps, minimal lines,
-- educational cards, and creator ticker captions. Detailed mode styling
-- lives in caption_tracks.style JSONB for backward-compatible evolution.

do $$
begin
  if not exists (
    select 1
    from pg_enum e
    join pg_type t on t.oid = e.enumtypid
    where t.typname = 'caption_preset_key'
      and e.enumlabel = 'impact_stamp'
  ) then
    alter type public.caption_preset_key add value 'impact_stamp';
  end if;

  if not exists (
    select 1
    from pg_enum e
    join pg_type t on t.oid = e.enumtypid
    where t.typname = 'caption_preset_key'
      and e.enumlabel = 'minimal_line'
  ) then
    alter type public.caption_preset_key add value 'minimal_line';
  end if;

  if not exists (
    select 1
    from pg_enum e
    join pg_type t on t.oid = e.enumtypid
    where t.typname = 'caption_preset_key'
      and e.enumlabel = 'educational_card'
  ) then
    alter type public.caption_preset_key add value 'educational_card';
  end if;

  if not exists (
    select 1
    from pg_enum e
    join pg_type t on t.oid = e.enumtypid
    where t.typname = 'caption_preset_key'
      and e.enumlabel = 'creator_ticker'
  ) then
    alter type public.caption_preset_key add value 'creator_ticker';
  end if;
end $$;
