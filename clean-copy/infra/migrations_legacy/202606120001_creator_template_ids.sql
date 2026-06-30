-- Add creator-facing render templates introduced after the Week 6 renderer launch.

do $$
begin
  if exists (select 1 from pg_type where typname = 'render_template_id') then
    if not exists (
      select 1
      from pg_enum
      where enumtypid = 'public.render_template_id'::regtype
        and enumlabel = 'talking_head_reel_v1'
    ) then
      alter type public.render_template_id add value 'talking_head_reel_v1';
    end if;

    if not exists (
      select 1
      from pg_enum
      where enumtypid = 'public.render_template_id'::regtype
        and enumlabel = 'product_promo_reel_v1'
    ) then
      alter type public.render_template_id add value 'product_promo_reel_v1';
    end if;
  end if;
end $$;
