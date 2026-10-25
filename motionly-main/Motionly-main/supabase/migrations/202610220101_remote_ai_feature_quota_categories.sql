-- Remote AI mode: per-plan quotas for the remote vision, embeddings and audio
-- features.
--
-- The model gateway records one cost_attribution_events row per successful
-- metered call (app.services.billing.PlanFeatureQuotaGuard) and sums quantity
-- per (workspace, provider, category) in the billing period to enforce each
-- plan's limit. The category check from 202606220008 allowed only the six
-- original cost categories, so those rows would be rejected. This widens the
-- check with one category per metered feature unit:
--
--   vision_object_images   images sent for object detection
--   vision_caption_images  images sent for captions
--   embedding_inputs       texts or images embedded
--   diarization_seconds    seconds of audio diarized
--   emotion_seconds        seconds of audio rated for voice emotion
--   stem_seconds           seconds of audio split into stems
--
-- Forward-only. The allowed set only grows, so existing rows stay valid.

begin;

-- Drop the existing category check whatever it was named (an inline column check
-- is auto-named cost_attribution_events_category_check).
do $$
declare
  existing record;
begin
  for existing in
    select con.conname
    from pg_constraint con
    where con.conrelid = 'public.cost_attribution_events'::regclass
      and con.contype = 'c'
      and pg_get_constraintdef(con.oid) ilike '%category%'
  loop
    execute format(
      'alter table public.cost_attribution_events drop constraint %I',
      existing.conname
    );
  end loop;
end
$$;

alter table public.cost_attribution_events
  add constraint cost_attribution_events_category_check
  check (
    category in (
      'tokens',
      'generated_seconds',
      'compute_cpu',
      'compute_gpu',
      'storage',
      'egress',
      'vision_object_images',
      'vision_caption_images',
      'embedding_inputs',
      'diarization_seconds',
      'emotion_seconds',
      'stem_seconds'
    )
  );

commit;
