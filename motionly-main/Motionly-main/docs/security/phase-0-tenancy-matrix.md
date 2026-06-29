# Phase 0 Route-Level Tenancy Matrix

This matrix documents the Phase 0 tenancy contract for protected API routes. Security
tests must fail closed: missing auth and invalid JWTs return `401`; invalid auth
configuration fails startup or returns `500`; cross-workspace objects return `403`
or `404`; worker routes require `X-Motionly-Worker-Token`.

| Route group | Auth required | Workspace source | Object tenancy check | Signed URL exposure | Worker token |
| --- | --- | --- | --- | --- | --- |
| `/projects` | JWT or explicit local bypass | `X-Motionly-Workspace-Id` after JWT verification | Project repository filters by `workspace_id` | n/a | n/a |
| `/projects/{id}/uploads` | JWT or explicit local bypass | `Actor.workspace_id` | Project and upload are fetched with `project_id` and `workspace_id` | Upload and asset URLs are signed only after upload ownership lookup | n/a |
| `/projects/{id}/plans` | JWT or explicit local bypass | `Actor.workspace_id` | Project, active upload, captions, plan IDs, and snapshots are fetched with `workspace_id` | n/a | n/a |
| `/projects/{id}/captions` | JWT or explicit local bypass | `Actor.workspace_id` | Project, upload, transcript, and caption track IDs are fetched with `workspace_id` | n/a | n/a |
| `/projects/{id}/broll` | JWT or explicit local bypass | `Actor.workspace_id` | Project, plan, slot, library asset, and generated asset IDs are fetched with `workspace_id` | B-roll upload/download URLs are signed only after workspace ownership lookup | n/a |
| `/projects/{id}/music` | JWT or explicit local bypass | `Actor.workspace_id` | Project plan and music selection are fetched with `workspace_id` | n/a | n/a |
| `/projects/{id}/effects` | JWT or explicit local bypass | `Actor.workspace_id` | Effects config and project plan are fetched with `workspace_id` | n/a | n/a |
| `/projects/{id}/renders` | JWT or explicit local bypass | `Actor.workspace_id` | Project, upload, caption, render job, event, export, and restore IDs are fetched with `workspace_id` | Export download URLs are signed only after export ownership lookup | n/a |
| `/projects/{id}/publish` | JWT or explicit local bypass | `Actor.workspace_id` | Project, export, connection, and publish jobs are fetched with `workspace_id`; export must match project | Publish worker signs export URL only after export ownership lookup | n/a |
| `/billing` | JWT or explicit local bypass | `Actor.workspace_id` | Subscription, usage, checkout customer, and portal are scoped to `workspace_id` | n/a | n/a |
| Internal worker routes | n/a | Request payload plus repository lookup | Worker payload IDs are checked against repository `workspace_id` and `project_id` | n/a | Required |

Additional Phase 0 evidence:

- JWT identity is never inferred from unsigned user or workspace headers.
- Outside `local` and `development`, `SUPABASE_JWT_SECRET`, `SUPABASE_URL`, and
  `SUPABASE_SERVICE_ROLE_KEY` are required so workspace membership can be verified.
- `SKIP_API_AUTH=true` is accepted only when `APP_ENV` is exactly `local` or
  `development`.
- Direct database/RLS coverage exists in `supabase/tests/phase_0_tenancy_rls_test.sql`
  and `supabase/tests/phase_9_rls_test.sql`.
