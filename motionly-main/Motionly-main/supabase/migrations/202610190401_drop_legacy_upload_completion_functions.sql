-- Task 17: one resumable, server-verified upload path for every media type.
-- The legacy project upload endpoints (POST /uploads/init and
-- POST /uploads/{video_id}/complete) and the one-piece B-roll and style
-- reference facades are gone. The only completion function those legacy
-- endpoints used were motionly_claim_legacy_upload and
-- motionly_finalize_legacy_upload (claim + finalize of the legacy one-shot
-- quota reservation). No remaining caller references them: the resumable
-- multipart path uses motionly_initialize_upload + the upload-session RPCs,
-- and upload cancellation still uses motionly_cancel_legacy_upload, so both of
-- those stay.
--
-- Plain DROP (no IF EXISTS, no CASCADE): each function was created by an
-- earlier migration, and an unexpected dependency must stop the release.

begin;

drop function public.motionly_claim_legacy_upload(uuid, uuid, uuid, uuid, bigint, timestamptz);
drop function public.motionly_finalize_legacy_upload(uuid, uuid, uuid, uuid, uuid, bigint, text);

commit;
