
> **Temporal mTLS/authz release gate:** the custom Temporal server image is never automatically activated without a completed same-VM overlap proof. Follow `docs/ops/temporal-mtls-canary.md`; `temporal_canary_gate.sh` refuses activation/migration when the proof is absent or incomplete. No live canary is claimed by this repository.
# Compose production deployment on one Azure VM

Motionly production has exactly one supported runtime: the digest-only `infra/docker-compose.gpu.yml` stack on one Azure VM. Managed-cluster, autoscaling, Railway, direct tag deployment, and VMSS paths are unsupported. A VMSS is explicitly unsupported until media state, PostgreSQL, Redis, and local model/cache storage have an approved shared-state/externalization design.

## One-time host bootstrap

1. Provision a Linux VM with Docker Compose v2, Azure CLI, Cosign, Python 3, the NVIDIA Container Toolkit, and a system-assigned managed identity. Restrict SSH; GitHub deployment uses Azure VM Run Command rather than SSH.
2. Grant the VM identity **Key Vault Secrets User** on the Motionly vault and **Storage Blob Data Reader** on the release container/account. Grant its application identity only GHCR package read access; store the resulting read-only GHCR token in Key Vault as `ghcr-read-token`.
3. From a reviewed checkout, run `sudo scripts/deploy/bootstrap_compose_host.sh`. It installs `/opt/motionly/agent/bin/apply-release` and creates `/etc/motionly/host.env` if absent.
4. Fill only non-secret resource identifiers and domains in `/etc/motionly/host.env`, then set it to `root:root` and `0600`. The agent requires `MOTIONLY_KEY_VAULT_NAME`, `MOTIONLY_RELEASE_STORAGE_ACCOUNT`, `MOTIONLY_GHCR_USERNAME`, `MOTIONLY_APP_DOMAIN`, `MOTIONLY_API_DOMAIN`, and `MOTIONLY_SUPABASE_DOMAIN`.
5. Create the documented Key Vault secrets: `postgres-password`, `redis-password`, `supabase-jwt-secret`, `supabase-anon-key`, `supabase-service-role-key`, `admin-api-token`, `worker-api-token`, `publish-token-encryption-key`, `publish-oauth-state-secret`, `groq-api-key`, `openrouter-api-key`, `runway-api-key`, optional `azure-openai-api-key`, `metrics-bearer-token`, `sentry-dsn`, and the read-only `ghcr-read-token`.

The VM agent obtains them only through managed identity, creates per-release `0700` directories and `0600` files, and mounts them as Compose secrets. GitHub Actions never sends a secret as a Run Command argument.

## Release flow

1. A `v*` tag runs the supply-chain workflow. It builds six linux/amd64 components, scans and signs each pushed `ghcr.io/...@sha256:...` image, and creates a signed release manifest mapping all ten Compose image variables to those digests.
2. `deploy-compose-vm.yml` runs automatically after a successful tag supply-chain run. It uses GitHub Actions OIDC (`azure/login`) to upload the signed manifest, certificate, signature, and configuration/migration bundle to Azure Blob Storage.
3. The staging job calls `apply-release --release-id <sha> --preflight` on the same single host. This validates blob access, manifest digest shape, bundle layout, and Cosign identity without changing containers or reading application secrets.
4. The `production` GitHub Environment must be configured with required reviewers. After approval, the workflow passes only the release SHA to Azure VM Run Command. The host validates the signed manifest, reads Key Vault values locally, performs `docker compose pull`, brings up the full topology, and checks API health.

Required GitHub configuration values are `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_VM_NAME`, and `AZURE_RELEASE_STORAGE_ACCOUNT`. They are non-secret GitHub Environment/repository variables. The Azure federated credential must be restricted to this repository and the deployment workflow/environment subject.

## Rollback and evidence

The host records `current` and `previous` release directories plus copies of their manifests under `/var/lib/motionly`. If activation health checks fail, the agent automatically activates the previous digest manifest. An operator can request the same rollback path through Run Command:

```sh
sudo /opt/motionly/agent/bin/apply-release --rollback
```

Do not use `docker compose pull` with tags, edit a release env file by hand, or reset volumes during rollback. Capture `docker compose logs`, the signed release manifest/certificate/signature, Key Vault audit records, and GitHub Environment approval history in the incident record.

## Secrets and access boundaries

- GitHub OIDC identities upload only non-secret release artifacts to Blob Storage and invoke Run Command.
- The VM identity reads artifacts and Key Vault values; it has no package write grant.
- The GHCR token is read-only and never appears in workflow logs or Run Command parameters.
- API and worker credentials are file-backed `/run/secrets/*`; only unavoidable Supabase/Redis bootstrap fields are written to the protected per-release env file.
- Keep one VM: local PostgreSQL, Redis, storage, model cache, and Compose volumes are host-affine by design.
