---
name: motionly-verify
description: Run the Motionly verification gates (API, web, worker, contracts, lint, neutrality, Tier gates) and report pass/fail
---

# Motionly verification gates

Scope or focus: $ARGUMENTS

All paths are relative to `motionly-main/Motionly-main`. The API interpreter is
`apps/api/.venv/bin/python` (Python 3.12 with the `dev` extra); `ffmpeg` is installed.

Run each gate and record its exact result line:

1. API tests: `cd apps/api && APP_ENV=test .venv/bin/python -m pytest -q -p no:cacheprovider`
2. API lint: `cd apps/api && .venv/bin/python -m ruff check app tests scripts`
3. OpenAPI contract: `cd apps/api && .venv/bin/python scripts/generate_openapi_contract.py --check`
4. Web tests: `cd apps/web && node --test tests/*.test.mjs`
5. Worker tests: `cd apps/worker && node --test src/*.test.mjs`
6. Typecheck: `pnpm -r typecheck`
7. Lint and format: `pnpm lint` and `pnpm format`
8. Neutrality: `python3 scripts/verify/vendor_neutrality.py --enforce`
9. Tier 1 gate: `python3 scripts/verify/tier1_gate.py`
10. Tier 2/3 gates: check each numbered item of "Tier 2 gate" and "Tier 3 gate" in
    `docs/provider-neutral-inference-implementation-plan.md` against code and the tests that prove it.

Gates that need Docker/Supabase/real providers (db, stack, browser, live runs) are reported as
"not run here" with the reason, never as passed.

Report a table: gate, command, result (PASS/FAIL/NOT RUN), evidence line. For each FAIL, give the
failing test ids and the root cause. Do not modify code unless the scope explicitly asks for fixes.
