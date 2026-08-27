-- Resolve pgcrypto from Supabase's extensions schema for already-created
-- security-definer functions. Fresh Task 7 installs also schema-qualify digest.

alter function public.motionly_start_workflow_run(
  uuid,uuid,uuid,integer,uuid,text,text,text,text,uuid,jsonb
) set search_path = public, extensions, pg_temp;

alter function public.motionly_complete_workflow_step(
  uuid,uuid,uuid,text,uuid,text,jsonb,text,text
) set search_path = public, extensions, pg_temp;

alter function public.motionly_fail_workflow_step(
  uuid,uuid,uuid,text,uuid,text,text,boolean
) set search_path = public, extensions, pg_temp;

-- Task 11 used the same restricted search path with unqualified pgcrypto calls.
alter function public.motionly_transition_ai_edit_proposal(
  uuid,uuid,uuid,text,jsonb
) set search_path = public, extensions, pg_temp;

alter function public.motionly_decide_ai_edit_proposal(
  uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid
) set search_path = public, extensions, pg_temp;
