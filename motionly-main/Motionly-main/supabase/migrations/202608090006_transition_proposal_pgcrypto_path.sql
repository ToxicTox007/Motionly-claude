-- Preserve pgcrypto resolution for the forward SQL-lint replacement of the
-- proposal transition function. Existing applied migrations remain untouched.
alter function public.motionly_transition_ai_edit_proposal(
  uuid, uuid, uuid, text, jsonb
) set search_path = public, extensions, pg_temp;
