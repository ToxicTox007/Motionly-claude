do $$
begin
  alter type public.render_job_status add value if not exists 'processing';
exception
  when duplicate_object then null;
end $$;
