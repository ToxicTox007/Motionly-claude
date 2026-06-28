# Archived migration tree (audit/history only)

**Do not add new migrations here.**

The canonical migration source for Motionly is:

```
supabase/migrations/
```

This directory (`infra/migrations_legacy/`) retains the former `infra/migrations/` tree
for audit and historical reference only. It is not applied by CI, Supabase CLI, or
updated deploy scripts.

All new schema changes must be added as forward migrations under `supabase/migrations/`.
Applied migration history must not be rewritten.
