---
name: motionly-plan-task
description: Implement one task of the Motionly provider-neutral inference plan to its acceptance criteria, with tests and verification
---

# Implement a provider-neutral inference plan task

Task to implement: $ARGUMENTS

## Where things are

- Repository root for all paths below: `motionly-main/Motionly-main` (Git root is two levels up).
- The plan: `docs/provider-neutral-inference-implementation-plan.md`. Read the task's own section in
  Section 4 (Files, Migration, Implementation, Tests, Acceptance criteria) and every Section 2.x it
  references (2.6 validation, 2.10 grammar, 2.11 model choice, ...). The plan is the specification.
- API: FastAPI in `apps/api/app/`, tests in `apps/api/tests/`. Web: Next.js in `apps/web/`, source
  contract tests in `apps/web/tests/*.test.mjs`. Migrations: `supabase/migrations/` with a matching
  `supabase/tests/*_test.sql` (required by `apps/api/tests/test_migration_contract_coverage.py`).

## Rules

1. Read existing code first and match its style, naming and libraries. Do not add dependencies.
2. Provider neutrality: no vendor names (Runway, OpenRouter, Groq, Jina, Google Vision, pyannoteAI,
   LALAL, ...) in core code, UI, infra or new migrations. Vendor names may appear only in
   `docs/` and registry example files.
3. Secrets are referenced by name; never return, log or render a secret value, base URL or
   provider model id to non-admin clients.
4. New API routes must be classified in `apps/api/tests/test_role_route_inventory.py` with the
   matching guard, and the OpenAPI contract regenerated:
   `cd apps/api && .venv/bin/python scripts/generate_openapi_contract.py` (then `--check`).
5. Tests use fakes only; real network is blocked by the test guard.
6. UI: accessible (`aria-*`, keyboard, visible text, never colour alone), matching the existing
   neumorphic components and the `apps/web/tests/neumorphic-guard-*.test.mjs` rules.
7. Stay inside the files your task owns. If you must touch a shared file (`app/main.py`,
   `model_gateway.py`, `openapi.json`, `test_role_route_inventory.py`), make minimal, additive edits.
8. Never run destructive git commands, never commit, never push.
9. When the task meets its acceptance criteria, update its `**Progress (YYYY-MM-DD):**` line in the
   plan (add one under the task heading if missing) stating what landed and how it was verified.

## Verify before reporting

Run from `apps/api` unless noted (Python is `apps/api/.venv/bin/python`, APP_ENV=test):

- Task tests: `APP_ENV=test .venv/bin/python -m pytest -q -p no:cacheprovider tests/<files>`
- Lint: `.venv/bin/python -m ruff check app tests scripts`
- Contract (if routes/schemas changed): `.venv/bin/python scripts/generate_openapi_contract.py --check`
- Web (if touched, from `apps/web`): `node --test tests/*.test.mjs` and `npx tsc --noEmit`
- Neutrality (from repo root): `python3 scripts/verify/vendor_neutrality.py --enforce`

Report: files changed, acceptance criteria met (with the test names that prove each), and any
criterion you could not meet with the reason.
