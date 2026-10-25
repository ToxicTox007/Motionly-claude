# Motionly documentation

This directory is the canonical home for maintained Motionly documentation. Links in source code, scripts, tests, and setup instructions should point here with repository-relative paths.

## Start here

- [Local development](reference/local-development.md) — create local environment files and start the Compose stack.
- [Operations](operations.md) — health checks, observability, deployment signals, and operational controls.
- [Compose VM deployment](ops/compose-vm-deployment.md) — the supported production deployment path.
- [Renderer feature matrix](renderer-feature-matrix.md) — renderer capability and compatibility evidence.

## Architecture and product

- [Technical documentation snapshot](generated/motionly_technical_documentation.md) — broad generated reference; treat implementation and focused runbooks as authoritative.
- [Editor collaboration](editor-collaboration.md) — collaboration and editing behavior.
- [Renderer feature matrix](renderer-feature-matrix.md) — supported renderer features and gates.
- [Provider-neutral inference implementation plan](provider-neutral-inference-implementation-plan.md) — tiered plan for routing every AI capability through a provider-neutral registry.
- [Inference registry](ai-registry/README.md) — operator guide: registry format, validation rules, secrets, fragments, enabling capabilities.
- [AI registry examples and template scenarios](ai-registry/examples/README.md) — example registry documents and the Task 17 template-port scenario schema.
- [AI live-run evidence log](ai-registry/live-runs.md) — mandatory live-provider runs recorded before Task 19 deletes a vendor adapter.

## Operations and deployment

- [Operations](operations.md)
- [Backups and restore](backups-restore.md)
- [Data retention](data-retention.md)
- [AWS infrastructure](ops/aws-infrastructure.md)
- [Compose VM deployment](ops/compose-vm-deployment.md)
- [GPU media workers](ops/gpu-media-workers.md)
- [Performance load tests](ops/performance-load-tests.md)
- [Performance query-plan evidence](ops/performance-query-plan-evidence.md)
- [PostgreSQL 17 upgrade](ops/postgresql-17-upgrade.md)
- [Supply-chain controls](ops/supply-chain.md)
- [Temporal mTLS canary](ops/temporal-mtls-canary.md)
- [Temporal task-queue migration](ops/temporal-queue-migration.md)

## Security and governance

- [Data handling](security/data-handling.md) — external providers and internal storage.
- [Runtime secrets](security/secrets.md) — secret-file names, permissions, and `/run/secrets` behavior.
- [Dependency advisories](security/dependency-advisories.md) — pinned fixes and known unpatched advisories.
- [Edit-plan integrity](security/edit-plan-integrity.md)
- [Phase 0 tenancy matrix](security/phase-0-tenancy-matrix.md)
- [Prelaunch review](prelaunch-review.md)

## Workers and rendering

- [Job idempotency matrix](workers/job-idempotency-matrix.md)
- [Render correctness](workers/render-correctness.md)

## Verification and historical reference

- [Hardening release progress](hardening-release-progress.md)
- [Original hardening-plan cross-check](original-hardening-plan-crosscheck.md)
- [Retired Azure cost-optimized plan](azure_cost_optimized_plan.md) — historical reference only; do not use for deployment.

## Maintenance rules

- Keep new maintained documentation in this tree; do not add ad hoc reports at the repository root.
- Use links relative to the file that contains them. Do not use workstation-specific paths such as `/workspaces/...`.
- Update this index whenever a Markdown file is added, renamed, or removed.
- Put generated material under `docs/generated/` and identify its source and generation command. Do not treat generated snapshots as the implementation source of truth.
