-- Task 16B pgTAP: resume_vars column for resumable blocking async_job
-- operations. It verifies the column shape (jsonb, NOT NULL, default '{}') and
-- the secret-value guard that mirrors the route_snapshot guard from Task 16A.
--
-- Forward-only guarantee for 202610240102_inference_operations_resume.sql.
begin;
select plan(6);

-- Shape -----------------------------------------------------------------------
select has_column('public', 'inference_operations', 'resume_vars',
  'resume_vars column exists');
select col_type_is('public', 'inference_operations', 'resume_vars', 'jsonb',
  'resume_vars is jsonb');
select col_not_null('public', 'inference_operations', 'resume_vars',
  'resume_vars is NOT NULL');
select col_default_is('public', 'inference_operations', 'resume_vars', '{}',
  'resume_vars defaults to an empty object');

-- Tenant fixtures -------------------------------------------------------------
insert into auth.users (id, email, raw_user_meta_data) values
  ('16b00000-0000-4000-8000-000000000001', 'ops@16b.test', '{}'::jsonb);
insert into public.workspaces (id, name, owner_user_id) values
  ('16b00000-0000-4000-8000-0000000000a1', 'Ops workspace 16b',
   '16b00000-0000-4000-8000-000000000001');

set local role service_role;

-- A non-secret resume payload (job id + captured URL) is accepted. -----------
select lives_ok(
  $$
    insert into public.inference_operations (
      workspace_id, capability, idempotency_key, provider_route_id,
      provider_model, provider_model_revision, route_snapshot, state, resume_vars
    ) values (
      '16b00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'resume-ok-1',
      'audio-diarization-remote', 'model-x', 'precision-v3', '{}'::jsonb,
      'submitted',
      '{"job_id": "abc-123", "upload_url": "https://files.example.invalid/x"}'::jsonb
    )
  $$,
  'resume_vars accepts non-secret captured identifiers'
);

-- A secret-bearing resume payload is rejected by the guard. ------------------
select throws_ok(
  $$
    insert into public.inference_operations (
      workspace_id, capability, idempotency_key, provider_route_id,
      provider_model, provider_model_revision, route_snapshot, state, resume_vars
    ) values (
      '16b00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'resume-bad-1',
      'audio-diarization-remote', 'model-x', 'precision-v3', '{}'::jsonb,
      'submitted', '{"api_key": "leaked"}'::jsonb
    )
  $$,
  '23514',
  null,
  'resume_vars rejects a secret-value key'
);

select * from finish();
rollback;
