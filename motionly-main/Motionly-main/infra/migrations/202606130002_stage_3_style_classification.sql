-- Stage 3 style classification.
-- Expands edit plan style classes while preserving legacy values for existing records.

alter type public.edit_plan_style add value if not exists 'motivational';
alter type public.edit_plan_style add value if not exists 'emotional';
alter type public.edit_plan_style add value if not exists 'cinematic';
alter type public.edit_plan_style add value if not exists 'educational';
alter type public.edit_plan_style add value if not exists 'storytelling';
alter type public.edit_plan_style add value if not exists 'dramatic';
alter type public.edit_plan_style add value if not exists 'fast_paced';
alter type public.edit_plan_style add value if not exists 'clean_minimal';
alter type public.edit_plan_style add value if not exists 'creator_influencer';
