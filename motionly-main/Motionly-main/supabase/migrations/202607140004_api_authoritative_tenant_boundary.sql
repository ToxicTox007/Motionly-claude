-- Roadmap Task 3: make the authenticated API/service role the only domain writer.
--
-- Existing rows are left deployable by adding tenant-consistency foreign keys as
-- NOT VALID. PostgreSQL still enforces those constraints for every new or updated
-- row; a later maintenance migration can validate them after production cleanup.

-- Every composite reference below terminates at a key which includes the tenant
-- and, where applicable, the project. The row id is already globally unique, so
-- these unique constraints cannot expose a new duplicate-data failure mode.
alter table public.projects
  add constraint task3_projects_workspace_id_id_key unique (workspace_id, id);
alter table public.uploaded_videos
  add constraint task3_uploads_workspace_project_id_id_key
  unique (workspace_id, project_id, id);
alter table public.transcripts
  add constraint task3_transcripts_workspace_project_id_id_key
  unique (workspace_id, project_id, id);
alter table public.caption_tracks
  add constraint task3_caption_tracks_workspace_project_id_id_key
  unique (workspace_id, project_id, id);
alter table public.edit_plans
  add constraint task3_edit_plans_workspace_project_id_id_key
  unique (workspace_id, project_id, id);
alter table public.render_jobs
  add constraint task3_render_jobs_workspace_project_id_id_key
  unique (workspace_id, project_id, id);
alter table public.exports
  add constraint task3_exports_workspace_project_id_id_key
  unique (workspace_id, project_id, id);
alter table public.stock_assets
  add constraint task3_stock_assets_workspace_project_id_id_key
  unique (workspace_id, project_id, id);
alter table public.music_tracks
  add constraint task3_music_tracks_workspace_project_id_id_key
  unique (workspace_id, project_id, id);
alter table public.publish_connections
  add constraint task3_publish_connections_workspace_id_id_key
  unique (workspace_id, id);

-- Core project -> upload -> transcript/captions/plans -> render -> export graph.
alter table public.uploaded_videos
  add constraint task3_uploads_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade not valid;

alter table public.projects
  add constraint task3_projects_active_video_tenant_fk
  foreign key (workspace_id, id, active_video_id)
  references public.uploaded_videos (workspace_id, project_id, id)
  on delete set null (active_video_id) not valid;

alter table public.upload_events
  add constraint task3_upload_events_video_tenant_fk
  foreign key (workspace_id, project_id, uploaded_video_id)
  references public.uploaded_videos (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.transcripts
  add constraint task3_transcripts_video_tenant_fk
  foreign key (workspace_id, project_id, uploaded_video_id)
  references public.uploaded_videos (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.transcript_words
  add constraint task3_transcript_words_transcript_tenant_fk
  foreign key (workspace_id, project_id, transcript_id)
  references public.transcripts (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.caption_tracks
  add constraint task3_caption_tracks_transcript_tenant_fk
  foreign key (workspace_id, project_id, transcript_id)
  references public.transcripts (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.caption_segments
  add constraint task3_caption_segments_track_tenant_fk
  foreign key (workspace_id, project_id, caption_track_id)
  references public.caption_tracks (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.edit_plans
  add constraint task3_edit_plans_transcript_tenant_fk
  foreign key (workspace_id, project_id, transcript_id)
  references public.transcripts (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.edit_plan_snapshots
  add constraint task3_plan_snapshots_plan_tenant_fk
  foreign key (workspace_id, project_id, source_plan_id)
  references public.edit_plans (workspace_id, project_id, id)
  on delete cascade not valid,
  add constraint task3_plan_snapshots_transcript_tenant_fk
  foreign key (workspace_id, project_id, transcript_id)
  references public.transcripts (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.render_jobs
  add constraint task3_render_jobs_video_tenant_fk
  foreign key (workspace_id, project_id, uploaded_video_id)
  references public.uploaded_videos (workspace_id, project_id, id)
  on delete cascade not valid,
  add constraint task3_render_jobs_caption_track_tenant_fk
  foreign key (workspace_id, project_id, caption_track_id)
  references public.caption_tracks (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.render_job_events
  add constraint task3_render_events_job_tenant_fk
  foreign key (workspace_id, project_id, render_job_id)
  references public.render_jobs (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.exports
  add constraint task3_exports_render_job_tenant_fk
  foreign key (workspace_id, project_id, render_job_id)
  references public.render_jobs (workspace_id, project_id, id)
  on delete cascade not valid,
  add constraint task3_exports_restored_from_tenant_fk
  foreign key (workspace_id, project_id, restored_from_export_id)
  references public.exports (workspace_id, project_id, id)
  on delete set null (restored_from_export_id) not valid;

-- Apply the same invariant to generated project layers and downstream publishing.
alter table public.stock_assets
  add constraint task3_stock_assets_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade not valid;

alter table public.broll_slots
  add constraint task3_broll_slots_plan_tenant_fk
  foreign key (workspace_id, project_id, edit_plan_id)
  references public.edit_plans (workspace_id, project_id, id)
  on delete cascade not valid,
  add constraint task3_broll_slots_asset_tenant_fk
  foreign key (workspace_id, project_id, selected_asset_id)
  references public.stock_assets (workspace_id, project_id, id)
  on delete set null (selected_asset_id) not valid;

alter table public.music_tracks
  add constraint task3_music_tracks_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade not valid;

alter table public.project_music_selections
  add constraint task3_music_selections_plan_tenant_fk
  foreign key (workspace_id, project_id, edit_plan_id)
  references public.edit_plans (workspace_id, project_id, id)
  on delete cascade not valid,
  add constraint task3_music_selections_track_tenant_fk
  foreign key (workspace_id, project_id, music_track_id)
  references public.music_tracks (workspace_id, project_id, id)
  on delete set null (music_track_id) not valid;

alter table public.visual_presets
  add constraint task3_visual_presets_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade not valid,
  add constraint task3_visual_presets_plan_tenant_fk
  foreign key (workspace_id, project_id, edit_plan_id)
  references public.edit_plans (workspace_id, project_id, id)
  on delete set null (edit_plan_id) not valid;

alter table public.project_control_settings
  add constraint task3_control_settings_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade not valid;

alter table public.artifact_versions
  add constraint task3_artifact_versions_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade not valid;

alter table public.video_manifests
  add constraint task3_video_manifests_upload_tenant_fk
  foreign key (workspace_id, project_id, uploaded_video_id)
  references public.uploaded_videos (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.segmentation_mattes
  add constraint task3_segmentation_mattes_upload_tenant_fk
  foreign key (workspace_id, project_id, uploaded_video_id)
  references public.uploaded_videos (workspace_id, project_id, id)
  on delete cascade not valid;

alter table public.phase5_resources
  add constraint task3_phase5_resources_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade not valid;

alter table public.publish_jobs
  add constraint task3_publish_jobs_export_tenant_fk
  foreign key (workspace_id, project_id, export_id)
  references public.exports (workspace_id, project_id, id)
  on delete cascade not valid,
  add constraint task3_publish_jobs_connection_tenant_fk
  foreign key (workspace_id, connection_id)
  references public.publish_connections (workspace_id, id)
  on delete restrict not valid;

alter table public.ai_edit_feedback_events
  add constraint task3_ai_feedback_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade not valid,
  add constraint task3_ai_feedback_plan_tenant_fk
  foreign key (workspace_id, project_id, edit_plan_id)
  references public.edit_plans (workspace_id, project_id, id)
  on delete set null (edit_plan_id) not valid;

-- Operational records can also carry domain references. Protect service-role
-- mistakes there even though clients never see or mutate these tables directly.
alter table public.provider_slo_events
  add constraint task3_provider_slo_project_requires_workspace
  check (project_id is null or workspace_id is not null) not valid,
  add constraint task3_provider_slo_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade not valid;

alter table public.cost_attribution_events
  add constraint task3_cost_events_render_requires_project
  check (render_job_id is null or project_id is not null) not valid,
  add constraint task3_cost_events_export_requires_project
  check (export_id is null or project_id is not null) not valid,
  add constraint task3_cost_events_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade not valid,
  add constraint task3_cost_events_render_tenant_fk
  foreign key (workspace_id, project_id, render_job_id)
  references public.render_jobs (workspace_id, project_id, id)
  on delete cascade not valid,
  add constraint task3_cost_events_export_tenant_fk
  foreign key (workspace_id, project_id, export_id)
  references public.exports (workspace_id, project_id, id)
  on delete set null (export_id) not valid;

-- Policy names have changed throughout the migration history. Discover and drop
-- every policy on the protected relations instead of relying on historical names.
-- Read policies are then rebuilt from one auditable tenant predicate.
do $task3$
declare
  protected_table text;
  existing_policy record;
  tenant_read_tables constant text[] := array[
    'projects',
    'uploaded_videos',
    'upload_events',
    'transcripts',
    'transcript_words',
    'caption_tracks',
    'caption_segments',
    'edit_plans',
    'edit_plan_snapshots',
    'render_jobs',
    'render_job_events',
    'exports',
    'stock_assets',
    'broll_slots',
    'music_tracks',
    'project_music_selections',
    'visual_presets',
    'project_control_settings',
    'artifact_versions',
    'video_manifests',
    'segmentation_mattes',
    'phase5_resources',
    'publish_jobs',
    'subscriptions',
    'usage_ledger',
    'billing_adjustments',
    'ai_edit_feedback_events'
  ];
  protected_tables constant text[] := tenant_read_tables || array[
    'licensed_music_tracks',
    'publish_connections',
    'stripe_webhook_events',
    'worker_heartbeats',
    'provider_slo_events',
    'cost_attribution_events',
    'storage_deletion_queue',
    'retention_runs',
    'launch_feedback'
  ];
begin
  foreach protected_table in array protected_tables loop
    execute format('alter table public.%I enable row level security', protected_table);

    for existing_policy in
      select policy.polname
      from pg_policy as policy
      where policy.polrelid = format('public.%I', protected_table)::regclass
    loop
      execute format(
        'drop policy %I on public.%I',
        existing_policy.polname,
        protected_table
      );
    end loop;

    execute format(
      'revoke all privileges on table public.%I from anon, authenticated',
      protected_table
    );
    execute format(
      'grant all privileges on table public.%I to service_role',
      protected_table
    );
  end loop;

  foreach protected_table in array tenant_read_tables loop
    execute format(
      'create policy %I on public.%I for select to authenticated using (public.is_workspace_member(workspace_id))',
      'tenant members read ' || protected_table,
      protected_table
    );
    execute format(
      'grant select on table public.%I to authenticated',
      protected_table
    );
  end loop;
end
$task3$;

-- Identity context is browser-readable but not directly mutable. Profile edits
-- continue through update_own_profile, and workspace membership changes remain
-- trusted service operations.
revoke all privileges on table public.profiles from anon, authenticated;
revoke all privileges on table public.workspaces from anon, authenticated;
revoke all privileges on table public.workspace_members from anon, authenticated;
grant select on table public.profiles to authenticated;
grant select on table public.workspaces to authenticated;
grant select on table public.workspace_members to authenticated;

-- Keep the profile self-service contract explicit. This is the only direct web
-- mutation RPC intentionally retained for authenticated users.
grant all privileges on table public.profiles to service_role;
grant all privileges on table public.workspaces to service_role;
grant all privileges on table public.workspace_members to service_role;
revoke all privileges on function public.update_own_profile(text, text, text)
  from public, anon;
grant execute on function public.update_own_profile(text, text, text)
  to authenticated, service_role;
