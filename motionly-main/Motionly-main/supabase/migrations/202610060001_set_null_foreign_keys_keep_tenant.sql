-- Task 2: three foreign keys used ON DELETE SET NULL on columns that are NOT
-- NULL, so deleting the referenced row raised a not-null violation instead:
--   * deleting a project failed while a library item saved from it existed
--     (the composite key also nulled asset_library_items.workspace_id);
--   * deleting an edit plan failed while a localized package used it
--     (the composite key also nulled workspace_id and project_id);
--   * exports.render_job_id is required, so SET NULL could never succeed.
-- The composite keys now null only their optional reference column. A render
-- job with exports cannot be deleted on its own (NO ACTION); deleting the
-- project or workspace still removes both in one statement.

begin;

alter table public.asset_library_items
  drop constraint asset_library_items_project_tenant_fk,
  add constraint asset_library_items_project_tenant_fk
    foreign key (workspace_id, source_project_id)
    references public.projects (workspace_id, id)
    on delete set null (source_project_id);

alter table public.localized_publish_packages
  drop constraint localized_publish_packages_plan_tenant_fk,
  add constraint localized_publish_packages_plan_tenant_fk
    foreign key (workspace_id, project_id, edit_plan_id)
    references public.edit_plans (workspace_id, project_id, id)
    on delete set null (edit_plan_id);

alter table public.exports
  drop constraint exports_render_job_id_fkey,
  add constraint exports_render_job_id_fkey
    foreign key (render_job_id)
    references public.render_jobs (id);

commit;
