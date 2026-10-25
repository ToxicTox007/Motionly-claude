-- Tier 2 / Task 30 (M6): user model choice settings.
--
-- Users choose, per capability, among the operator's selectable registry routes
-- (plan Section 2.11, invariant 12). A choice selects a route; it never adds an
-- endpoint, model or key. Two settings tables hold the choice:
--
--   * workspace_model_settings: the workspace default, the allow-list members
--     may pick from, and a lock that stops projects overriding it. Editable by
--     owners and admins.
--   * project_model_settings: a per-project override, editable by any workspace
--     member who can edit the project.
--
-- The option id stored in either table is a registry route id (opaque text);
-- the resolver (app/services/model_choice.py) validates it against the live
-- runtime at read time, so a dangling or now-invalid option simply falls back to
-- the next resolution level. No foreign key to the registry exists because the
-- file registry has no database rows, and the database registry (Task 20) is an
-- optional, additive source.
--
-- The capability column is checked against the 14 fine-grained Section 2.1 ids,
-- exactly as inference_operations (202610240101) does.
--
-- Security posture: API-authoritative (plan Section 2.6, the pattern of
-- 202610240101_inference_operations.sql). Clients never read or write these
-- tables directly: anon and authenticated get nothing; service_role manages
-- everything. The restricted worker role (Task 18) gets SELECT only, because the
-- gateway's model-choice resolver runs inside the workers too (visual_job,
-- matte_job and the domain jobs resolve the preferred route), and a worker that
-- is granted an RLS table needs a worker row policy or it reads no rows
-- (supabase/tests/task_18_worker_row_access_test.sql). The worker never writes a
-- choice: only the API/ops surface does.
--
-- Tenant integrity: project_model_settings references projects on the composite
-- (workspace_id, id) key, the same pattern as 202610240101 / 202607140004, so a
-- project row can never point outside its workspace.
--
-- Forward-only. Not edited after it is applied (scripts/check_forward_migrations.sh).
begin;

-- 1. Workspace-level choice, one row per (workspace, capability).
create table public.workspace_model_settings (
  workspace_id uuid not null references public.workspaces (id) on delete cascade,
  capability text not null,
  -- The workspace's chosen option (a registry route id). null means "no explicit
  -- workspace default", so resolution uses the operator default.
  default_option_id text,
  -- The options members may pick (registry route ids). null means "every option
  -- the plan allows"; an empty array means "none" (members cannot override).
  allowed_option_ids text[],
  -- When true, projects cannot override the workspace default.
  locked boolean not null default false,
  updated_by uuid,
  updated_at timestamptz not null default now(),
  primary key (workspace_id, capability),
  constraint workspace_model_settings_capability_check check (
    capability in (
      'planning.chat',
      'transcription.speech_to_text',
      'vision.caption',
      'vision.object_detection',
      'embeddings.text',
      'embeddings.image',
      'audio.diarization',
      'audio.emotion',
      'audio.stem_separation',
      'image.generation',
      'video.generation.submit',
      'video.generation.status',
      'matte.segmentation',
      'audio.sound_classification'
    )
  ),
  -- Bound a stored option id so a caller cannot persist an arbitrarily long
  -- value; route ids are short identifiers.
  constraint workspace_model_settings_default_option_length_check check (
    default_option_id is null or char_length(default_option_id) <= 200
  )
);

-- 2. Project-level override, one row per (project, capability).
create table public.project_model_settings (
  workspace_id uuid not null references public.workspaces (id) on delete cascade,
  project_id uuid not null,
  capability text not null,
  -- The project's chosen option (a registry route id). A row exists only when a
  -- project override is set; DELETE removes the override.
  option_id text not null,
  updated_by uuid,
  updated_at timestamptz not null default now(),
  primary key (project_id, capability),
  constraint project_model_settings_capability_check check (
    capability in (
      'planning.chat',
      'transcription.speech_to_text',
      'vision.caption',
      'vision.object_detection',
      'embeddings.text',
      'embeddings.image',
      'audio.diarization',
      'audio.emotion',
      'audio.stem_separation',
      'image.generation',
      'video.generation.submit',
      'video.generation.status',
      'matte.segmentation',
      'audio.sound_classification'
    )
  ),
  constraint project_model_settings_option_length_check check (
    char_length(option_id) <= 200
  )
);

-- Tenant integrity: the project reference terminates at (workspace_id, id), the
-- same composite key pattern as 202610240101 / 202607140004. A present project
-- must be in this workspace, and deleting the project removes its overrides.
alter table public.project_model_settings
  add constraint project_model_settings_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade;

-- Standard updated_at triggers (public.touch_updated_at, 202605300001).
create trigger workspace_model_settings_touch_updated_at
before update on public.workspace_model_settings
for each row execute function public.touch_updated_at();
create trigger project_model_settings_touch_updated_at
before update on public.project_model_settings
for each row execute function public.touch_updated_at();

-- 3. Row level security: API-authoritative. Clients never see these tables.
alter table public.workspace_model_settings enable row level security;
alter table public.project_model_settings enable row level security;
revoke all privileges on table public.workspace_model_settings from anon, authenticated;
revoke all privileges on table public.project_model_settings from anon, authenticated;
grant all privileges on table public.workspace_model_settings to service_role;
grant all privileges on table public.project_model_settings to service_role;

drop policy if exists "Service role manages workspace_model_settings"
  on public.workspace_model_settings;
create policy "Service role manages workspace_model_settings"
  on public.workspace_model_settings
  for all to service_role using (true) with check (true);
drop policy if exists "Service role manages project_model_settings"
  on public.project_model_settings;
create policy "Service role manages project_model_settings"
  on public.project_model_settings
  for all to service_role using (true) with check (true);

-- 4. Least-privilege worker grant (pattern: 202610240101_inference_operations.sql).
-- The model-choice resolver resolves the preferred route inside the workers, so
-- worker code reads both tables. It never writes a choice, so SELECT only.
-- apps/api/tests/test_worker_db_grants.py checks this allowlist against
-- worker-reachable code in both directions.
grant select on public.workspace_model_settings to motionly_worker;
grant select on public.project_model_settings to motionly_worker;

-- The worker does not bypass RLS and processes jobs for every workspace, so each
-- RLS table it is granted needs a worker row policy or it sees no rows. Read-only
-- policies (no WITH CHECK): the worker never inserts or updates a choice.
-- supabase/tests/task_18_worker_row_access_test.sql asserts that exactly the RLS
-- tables granted to the worker carry such a policy.
create policy motionly_worker_reads_workspace_model_settings
  on public.workspace_model_settings
  for select to motionly_worker using (true);
create policy motionly_worker_reads_project_model_settings
  on public.project_model_settings
  for select to motionly_worker using (true);

commit;
