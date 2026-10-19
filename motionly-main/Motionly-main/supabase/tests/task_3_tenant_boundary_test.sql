begin;
select plan(36);

insert into auth.users (id, email, raw_user_meta_data)
values
  ('12000000-0000-4000-8000-000000000001', 'task3-owner-a@rls.test', '{}'::jsonb),
  ('12000000-0000-4000-8000-000000000002', 'task3-owner-b@rls.test', '{}'::jsonb);

insert into public.workspaces (id, name, owner_user_id)
values
  ('32000000-0000-4000-8000-000000000001', 'Task 3 workspace A', '12000000-0000-4000-8000-000000000001'),
  ('32000000-0000-4000-8000-000000000002', 'Task 3 workspace B', '12000000-0000-4000-8000-000000000002');

insert into public.workspace_members (workspace_id, user_id, role, status)
values
  ('32000000-0000-4000-8000-000000000001', '12000000-0000-4000-8000-000000000001', 'owner', 'active'),
  ('32000000-0000-4000-8000-000000000002', '12000000-0000-4000-8000-000000000002', 'owner', 'active');

select ok(
  not exists (
    select 1
    from unnest(array[
      'projects', 'uploaded_videos', 'upload_events', 'transcripts',
      'transcript_words', 'caption_tracks', 'caption_segments', 'edit_plans',
      'edit_plan_snapshots', 'render_jobs', 'render_job_events', 'exports',
      'stock_assets', 'broll_slots', 'music_tracks', 'licensed_music_tracks',
      'project_music_selections', 'visual_presets', 'project_control_settings',
      'artifact_versions', 'video_manifests', 'segmentation_mattes',
      'phase5_resources', 'subscriptions',
      'usage_ledger', 'stripe_webhook_events', 'billing_adjustments',
      'ai_edit_feedback_events', 'worker_heartbeats', 'provider_slo_events',
      'cost_attribution_events', 'storage_deletion_queue', 'retention_runs',
      'launch_feedback'
    ]) as target(table_name)
    where has_table_privilege('authenticated', format('public.%I', table_name), 'INSERT')
       or has_table_privilege('authenticated', format('public.%I', table_name), 'UPDATE')
       or has_table_privilege('authenticated', format('public.%I', table_name), 'DELETE')
       or has_table_privilege('authenticated', format('public.%I', table_name), 'TRUNCATE')
  ),
  'authenticated has no mutation privilege on any protected domain or ops table'
);

select ok(
  not exists (
    select 1
    from pg_policy as policy
    join pg_class as relation on relation.oid = policy.polrelid
    join pg_namespace as namespace on namespace.oid = relation.relnamespace
    where namespace.nspname = 'public'
      and relation.relname = any(array[
        'projects', 'uploaded_videos', 'upload_events', 'transcripts',
        'transcript_words', 'caption_tracks', 'caption_segments', 'edit_plans',
        'edit_plan_snapshots', 'render_jobs', 'render_job_events', 'exports',
        'stock_assets', 'broll_slots', 'music_tracks', 'licensed_music_tracks',
        'project_music_selections', 'visual_presets', 'project_control_settings',
        'artifact_versions', 'video_manifests', 'segmentation_mattes',
        'phase5_resources', 'subscriptions',
        'usage_ledger', 'stripe_webhook_events', 'billing_adjustments',
        'ai_edit_feedback_events', 'worker_heartbeats', 'provider_slo_events',
        'cost_attribution_events', 'storage_deletion_queue', 'retention_runs',
        'launch_feedback'
      ])
      and policy.polcmd <> 'r'
  ),
  'protected tables retain no insert, update, delete, or all-command RLS policy'
);

select ok(
  not exists (
    select 1
    from unnest(array[
      'projects', 'uploaded_videos', 'upload_events', 'transcripts',
      'transcript_words', 'caption_tracks', 'caption_segments', 'edit_plans',
      'edit_plan_snapshots', 'render_jobs', 'render_job_events', 'exports',
      'stock_assets', 'broll_slots', 'music_tracks', 'licensed_music_tracks',
      'project_music_selections', 'visual_presets', 'project_control_settings',
      'artifact_versions', 'video_manifests', 'segmentation_mattes',
      'phase5_resources', 'subscriptions',
      'usage_ledger', 'stripe_webhook_events', 'billing_adjustments',
      'ai_edit_feedback_events', 'worker_heartbeats', 'provider_slo_events',
      'cost_attribution_events', 'storage_deletion_queue', 'retention_runs',
      'launch_feedback'
    ]) as target(table_name)
    where not has_table_privilege('service_role', format('public.%I', table_name), 'INSERT')
       or not has_table_privilege('service_role', format('public.%I', table_name), 'UPDATE')
       or not has_table_privilege('service_role', format('public.%I', table_name), 'DELETE')
  ),
  'service role has API mutation privileges on every protected table'
);

select ok(
  has_function_privilege(
    'authenticated',
    'public.update_own_profile(text,text,text)',
    'EXECUTE'
  ),
  'authenticated retains the profile self-update RPC'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.motionly_create_active_edit_plan(uuid,uuid,uuid,integer,public.edit_plan_status,public.edit_plan_style,integer,public.broll_density,text,text,text,text,text,text,text,integer,text,text,text,integer,numeric,jsonb,jsonb,jsonb)',
    'EXECUTE'
  ),
  'authenticated cannot bypass the boundary through the plan mutation RPC'
);

set local role service_role;

select lives_ok(
  $$
  insert into public.projects (id, workspace_id, created_by, title)
  values ('42000000-0000-4000-8000-000000000001', '32000000-0000-4000-8000-000000000001', '12000000-0000-4000-8000-000000000001', 'Task 3 project A');

  insert into public.uploaded_videos (
    id, workspace_id, project_id, storage_bucket, storage_path,
    original_filename, mime_type, byte_size, file_size_bytes
  ) values (
    '52000000-0000-4000-8000-000000000001',
    '32000000-0000-4000-8000-000000000001',
    '42000000-0000-4000-8000-000000000001',
    'raw-uploads', 'task3/a.mp4', 'a.mp4', 'video/mp4', 100, 100
  );

  insert into public.transcripts (
    id, workspace_id, project_id, uploaded_video_id, provider, model
  ) values (
    '62000000-0000-4000-8000-000000000001',
    '32000000-0000-4000-8000-000000000001',
    '42000000-0000-4000-8000-000000000001',
    '52000000-0000-4000-8000-000000000001', 'fixture', 'fixture'
  );

  insert into public.caption_tracks (
    id, workspace_id, project_id, transcript_id, preset
  ) values (
    '72000000-0000-4000-8000-000000000001',
    '32000000-0000-4000-8000-000000000001',
    '42000000-0000-4000-8000-000000000001',
    '62000000-0000-4000-8000-000000000001', 'founder_clean'
  );

  insert into public.edit_plans (
    id, workspace_id, project_id, transcript_id, version, style,
    target_duration_s, provider, model, cache_key, schema_hash, plan, summary
  ) values (
    '82000000-0000-4000-8000-000000000001',
    '32000000-0000-4000-8000-000000000001',
    '42000000-0000-4000-8000-000000000001',
    '62000000-0000-4000-8000-000000000001', 1, 'founder',
    30, 'fixture', 'fixture', 'task3-a', 'task3-a', '{}'::jsonb, '{}'::jsonb
  );

  insert into public.render_jobs (
    id, workspace_id, project_id, uploaded_video_id, caption_track_id, planner_version
  ) values (
    '92000000-0000-4000-8000-000000000001',
    '32000000-0000-4000-8000-000000000001',
    '42000000-0000-4000-8000-000000000001',
    '52000000-0000-4000-8000-000000000001',
    '72000000-0000-4000-8000-000000000001', 'task3'
  );

  insert into public.exports (
    id, workspace_id, project_id, render_job_id, version, storage_bucket, storage_path
  ) values (
    'a2000000-0000-4000-8000-000000000001',
    '32000000-0000-4000-8000-000000000001',
    '42000000-0000-4000-8000-000000000001',
    '92000000-0000-4000-8000-000000000001', 1, 'exports', 'task3/a-export.mp4'
  );
  $$,
  'service-role API can write the complete project-to-export graph'
);

select lives_ok(
  $$
  insert into public.projects (id, workspace_id, created_by, title)
  values ('42000000-0000-4000-8000-000000000002', '32000000-0000-4000-8000-000000000002', '12000000-0000-4000-8000-000000000002', 'Task 3 project B');

  insert into public.uploaded_videos (
    id, workspace_id, project_id, storage_bucket, storage_path,
    original_filename, mime_type, byte_size, file_size_bytes
  ) values (
    '52000000-0000-4000-8000-000000000002',
    '32000000-0000-4000-8000-000000000002',
    '42000000-0000-4000-8000-000000000002',
    'raw-uploads', 'task3/b.mp4', 'b.mp4', 'video/mp4', 100, 100
  );

  insert into public.transcripts (
    id, workspace_id, project_id, uploaded_video_id, provider, model
  ) values (
    '62000000-0000-4000-8000-000000000002',
    '32000000-0000-4000-8000-000000000002',
    '42000000-0000-4000-8000-000000000002',
    '52000000-0000-4000-8000-000000000002', 'fixture', 'fixture'
  );

  insert into public.caption_tracks (
    id, workspace_id, project_id, transcript_id, preset
  ) values (
    '72000000-0000-4000-8000-000000000002',
    '32000000-0000-4000-8000-000000000002',
    '42000000-0000-4000-8000-000000000002',
    '62000000-0000-4000-8000-000000000002', 'founder_clean'
  );

  insert into public.edit_plans (
    id, workspace_id, project_id, transcript_id, version, style,
    target_duration_s, provider, model, cache_key, schema_hash, plan, summary
  ) values (
    '82000000-0000-4000-8000-000000000002',
    '32000000-0000-4000-8000-000000000002',
    '42000000-0000-4000-8000-000000000002',
    '62000000-0000-4000-8000-000000000002', 1, 'founder',
    30, 'fixture', 'fixture', 'task3-b', 'task3-b', '{}'::jsonb, '{}'::jsonb
  );

  insert into public.render_jobs (
    id, workspace_id, project_id, uploaded_video_id, caption_track_id, planner_version
  ) values (
    '92000000-0000-4000-8000-000000000002',
    '32000000-0000-4000-8000-000000000002',
    '42000000-0000-4000-8000-000000000002',
    '52000000-0000-4000-8000-000000000002',
    '72000000-0000-4000-8000-000000000002', 'task3'
  );

  insert into public.exports (
    id, workspace_id, project_id, render_job_id, version, storage_bucket, storage_path
  ) values (
    'a2000000-0000-4000-8000-000000000002',
    '32000000-0000-4000-8000-000000000002',
    '42000000-0000-4000-8000-000000000002',
    '92000000-0000-4000-8000-000000000002', 1, 'exports', 'task3/b-export.mp4'
  );
  $$,
  'service-role API can write an independent second-tenant graph'
);

select lives_ok(
  $$insert into public.launch_feedback (category, message, page_url)
    values ('general', 'Task 3 service-role feedback submission', '/feedback')$$,
  'API-backed genuine feedback submission still works through service role'
);

select is(
  (select count(*)::integer from public.exports where id in (
    'a2000000-0000-4000-8000-000000000001',
    'a2000000-0000-4000-8000-000000000002'
  )),
  2,
  'service role reads both tenant export rows'
);

select throws_ok(
  $$insert into public.uploaded_videos (
      id, workspace_id, project_id, storage_bucket, storage_path,
      original_filename, mime_type, byte_size, file_size_bytes
    ) values (
      '52000000-0000-4000-8000-000000000099',
      '32000000-0000-4000-8000-000000000001',
      '42000000-0000-4000-8000-000000000002',
      'raw-uploads', 'task3/forged.mp4', 'forged.mp4', 'video/mp4', 100, 100
    )$$,
  '23503', null,
  'service role cannot combine a workspace with another tenant project'
);

select throws_ok(
  $$update public.projects
    set active_video_id = '52000000-0000-4000-8000-000000000002'
    where id = '42000000-0000-4000-8000-000000000001'$$,
  '23503', null,
  'active_video_id must identify an upload in the same workspace and project'
);

select throws_ok(
  $$insert into public.transcripts (
      id, workspace_id, project_id, uploaded_video_id, provider, model
    ) values (
      '62000000-0000-4000-8000-000000000099',
      '32000000-0000-4000-8000-000000000001',
      '42000000-0000-4000-8000-000000000001',
      '52000000-0000-4000-8000-000000000002', 'fixture', 'fixture'
    )$$,
  '23503', null,
  'transcript upload reference rejects a cross-tenant combination'
);

select throws_ok(
  $$insert into public.caption_tracks (
      id, workspace_id, project_id, transcript_id, preset
    ) values (
      '72000000-0000-4000-8000-000000000099',
      '32000000-0000-4000-8000-000000000001',
      '42000000-0000-4000-8000-000000000001',
      '62000000-0000-4000-8000-000000000002', 'founder_clean'
    )$$,
  '23503', null,
  'caption transcript reference rejects a cross-tenant combination'
);

select throws_ok(
  $$insert into public.edit_plans (
      id, workspace_id, project_id, transcript_id, version, style,
      target_duration_s, provider, model, cache_key, schema_hash, plan, summary
    ) values (
      '82000000-0000-4000-8000-000000000099',
      '32000000-0000-4000-8000-000000000001',
      '42000000-0000-4000-8000-000000000001',
      '62000000-0000-4000-8000-000000000002', 2, 'founder', 30,
      'fixture', 'fixture', 'task3-forged', 'task3-forged', '{}'::jsonb, '{}'::jsonb
    )$$,
  '23503', null,
  'edit plan transcript reference rejects a cross-tenant combination'
);

select throws_ok(
  $$insert into public.render_jobs (
      id, workspace_id, project_id, uploaded_video_id, caption_track_id, planner_version
    ) values (
      '92000000-0000-4000-8000-000000000098',
      '32000000-0000-4000-8000-000000000001',
      '42000000-0000-4000-8000-000000000001',
      '52000000-0000-4000-8000-000000000002',
      '72000000-0000-4000-8000-000000000001', 'task3'
    )$$,
  '23503', null,
  'render uploaded_video_id rejects a cross-tenant combination'
);

select throws_ok(
  $$insert into public.render_jobs (
      id, workspace_id, project_id, uploaded_video_id, caption_track_id, planner_version
    ) values (
      '92000000-0000-4000-8000-000000000099',
      '32000000-0000-4000-8000-000000000001',
      '42000000-0000-4000-8000-000000000001',
      '52000000-0000-4000-8000-000000000001',
      '72000000-0000-4000-8000-000000000002', 'task3'
    )$$,
  '23503', null,
  'render caption_track_id rejects a cross-tenant combination'
);

select throws_ok(
  $$insert into public.exports (
      id, workspace_id, project_id, render_job_id, version, storage_bucket, storage_path
    ) values (
      'a2000000-0000-4000-8000-000000000099',
      '32000000-0000-4000-8000-000000000001',
      '42000000-0000-4000-8000-000000000001',
      '92000000-0000-4000-8000-000000000002', 2, 'exports', 'task3/forged.mp4'
    )$$,
  '23503', null,
  'export render_job_id rejects a cross-tenant combination'
);

select throws_ok(
  $$insert into public.exports (
      id, workspace_id, project_id, render_job_id, restored_from_export_id,
      version, storage_bucket, storage_path
    ) values (
      'a2000000-0000-4000-8000-000000000098',
      '32000000-0000-4000-8000-000000000001',
      '42000000-0000-4000-8000-000000000001',
      '92000000-0000-4000-8000-000000000001',
      'a2000000-0000-4000-8000-000000000002',
      2, 'exports', 'task3/forged-restore.mp4'
    )$$,
  '23503', null,
  'restored export reference rejects a cross-tenant combination'
);

select is(
  (
    select count(*)::integer
    from pg_constraint
    where conname in (
      'task3_cost_events_render_requires_project',
      'task3_cost_events_export_requires_project'
    )
      and contype = 'c'
  ),
  2,
  'cost attribution render and export references require a project identity'
);

select ok(
  (
    select bool_and(not convalidated)
    from pg_constraint
    where conname in (
      'task3_cost_events_render_requires_project',
      'task3_cost_events_export_requires_project'
    )
  ),
  'cost attribution reference guards are safe NOT VALID constraints'
);

select throws_ok(
  $$insert into public.cost_attribution_events (
      workspace_id, project_id, render_job_id, source_id, provider,
      category, quantity, unit, unit_cost_usd
    ) values (
      '32000000-0000-4000-8000-000000000001', null,
      '92000000-0000-4000-8000-000000000002', 'task3-null-project-render',
      'test', 'compute_gpu', 1, 'second', 0
    )$$,
  '23514', null,
  'cost event cannot bypass render tenant identity with a null project_id'
);

select throws_ok(
  $$insert into public.cost_attribution_events (
      workspace_id, project_id, export_id, source_id, provider,
      category, quantity, unit, unit_cost_usd
    ) values (
      '32000000-0000-4000-8000-000000000001', null,
      'a2000000-0000-4000-8000-000000000002', 'task3-null-project-export',
      'test', 'storage', 1, 'byte', 0
    )$$,
  '23514', null,
  'cost event cannot bypass export tenant identity with a null project_id'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '12000000-0000-4000-8000-000000000001', true);

select throws_ok(
  $$insert into public.projects (workspace_id, created_by, title)
    values (
      '32000000-0000-4000-8000-000000000001',
      '12000000-0000-4000-8000-000000000001',
      'direct client project'
    )$$,
  '42501', null,
  'authenticated member cannot directly insert a project'
);

select throws_ok(
  $$update public.projects
    set title = 'direct client update'
    where id = '42000000-0000-4000-8000-000000000001'$$,
  '42501', null,
  'authenticated member cannot directly update a project'
);

select throws_ok(
  $$delete from public.projects
    where id = '42000000-0000-4000-8000-000000000001'$$,
  '42501', null,
  'authenticated member cannot directly delete a project'
);

select throws_ok(
  $$insert into public.uploaded_videos (
      workspace_id, project_id, storage_bucket, storage_path,
      original_filename, mime_type, byte_size, file_size_bytes
    ) values (
      '32000000-0000-4000-8000-000000000001',
      '42000000-0000-4000-8000-000000000001',
      'raw-uploads', 'task3/direct.mp4', 'direct.mp4', 'video/mp4', 100, 100
    )$$,
  '42501', null,
  'authenticated member cannot directly insert an upload'
);

select throws_ok(
  $$insert into public.launch_feedback (category, message)
    values ('general', 'A valid but direct feedback attempt')$$,
  '42501', null,
  'feedback table remains API-only rather than a direct client write surface'
);

select is(
  (select count(*)::integer from public.projects
   where id = '42000000-0000-4000-8000-000000000001'),
  1,
  'authenticated member retains SELECT access to own project'
);

select is(
  (select count(*)::integer from public.projects
   where id = '42000000-0000-4000-8000-000000000002'),
  0,
  'authenticated member cannot read another tenant project'
);

select is(
  (select count(*)::integer from public.uploaded_videos
   where id = '52000000-0000-4000-8000-000000000001'),
  1,
  'authenticated member retains SELECT access to own upload'
);

select is(
  (select count(*)::integer from public.uploaded_videos
   where id = '52000000-0000-4000-8000-000000000002'),
  0,
  'authenticated member cannot read another tenant upload'
);

select lives_ok(
  $$select public.update_own_profile('Task 3 Owner', 'Motionly', 'Creator')$$,
  'profile self-update RPC remains callable'
);

select is(
  (select full_name from public.profiles
   where id = '12000000-0000-4000-8000-000000000001'),
  'Task 3 Owner',
  'profile self-update RPC still mutates only the caller profile'
);

reset role;

select is(
  (
    select count(*)::integer
    from pg_constraint
    where conname = any(array[
      'task3_uploads_project_tenant_fk',
      'task3_projects_active_video_tenant_fk',
      'task3_transcripts_video_tenant_fk',
      'task3_caption_tracks_transcript_tenant_fk',
      'task3_edit_plans_transcript_tenant_fk',
      'task3_render_jobs_video_tenant_fk',
      'task3_render_jobs_caption_track_tenant_fk',
      'task3_exports_render_job_tenant_fk'
    ])
      and contype = 'f'
      and not convalidated
  ),
  8,
  'core tenant-consistency foreign keys are present as safe NOT VALID constraints'
);

select ok(
  (
    select pg_get_constraintdef(oid) like '%(workspace_id, id, active_video_id)%'
       and pg_get_constraintdef(oid) like '%uploaded_videos(workspace_id, project_id, id)%'
    from pg_constraint
    where conname = 'task3_projects_active_video_tenant_fk'
  ),
  'active_video_id composite FK binds workspace and project identity'
);

select is(
  (
    select count(*)::integer
    from pg_constraint
    where conname in (
      'task3_render_jobs_video_tenant_fk',
      'task3_render_jobs_caption_track_tenant_fk'
    )
      and pg_get_constraintdef(oid) like 'FOREIGN KEY (workspace_id, project_id, %'
  ),
  2,
  'render upload and caption references both carry tenant and project identity'
);

select * from finish();
rollback;
