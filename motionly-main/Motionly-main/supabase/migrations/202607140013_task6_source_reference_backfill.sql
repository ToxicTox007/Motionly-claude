-- Task 6 compatibility backfill: existing source uploads must also use durable
-- references so browser and worker signing cannot fall back to raw storage paths.
insert into public.object_references (
  workspace_id,
  project_id,
  storage_bucket,
  storage_path,
  owner_type,
  owner_id,
  object_type,
  mime_type,
  size_bytes,
  sha256,
  status
)
select
  upload.workspace_id,
  upload.project_id,
  upload.storage_bucket,
  upload.storage_path,
  'uploaded_video',
  upload.id,
  'source_video',
  upload.mime_type,
  coalesce(upload.verified_size_bytes, upload.file_size_bytes, upload.byte_size),
  upload.verified_sha256,
  'active'
from public.uploaded_videos as upload
where upload.source_object_reference_id is null
  and upload.status in ('uploaded', 'processing', 'ready')
  and coalesce(upload.verified_size_bytes, upload.file_size_bytes, upload.byte_size) > 0
  and upload.storage_path like (
    'workspaces/' || upload.workspace_id || '/projects/' || upload.project_id || '/%'
  )
on conflict (storage_bucket, storage_path) do nothing;

update public.uploaded_videos as upload
set source_object_reference_id = reference.id,
    updated_at = now()
from public.object_references as reference
where upload.source_object_reference_id is null
  and upload.status in ('uploaded', 'processing', 'ready')
  and reference.workspace_id = upload.workspace_id
  and reference.project_id = upload.project_id
  and reference.storage_bucket = upload.storage_bucket
  and reference.storage_path = upload.storage_path
  and reference.owner_type = 'uploaded_video'
  and reference.owner_id = upload.id
  and reference.object_type = 'source_video'
  and reference.status = 'active';
