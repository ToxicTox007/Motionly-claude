# Runtime secrets

The single-VM release agent creates a release-specific `SECRETS_DIR` with mode `0700` and writes each secret file with mode `0600`. Values come from Azure Key Vault through the VM managed identity; Compose files and GitHub Actions command arguments must not carry secret values.

The release process must never commit a local secret directory. Application containers read credentials from read-only `/run/secrets/*` mounts.

## Secret files

Create or retrieve these file names before starting `infra/docker-compose.gpu.yml`:

- `postgres_password`
- `redis_password`
- `supabase_service_role_key`
- `supabase_jwt_secret`
- `admin_api_token`
- `worker_api_token`
- `publish_token_encryption_key`
- `publish_oauth_state_secret`
- `groq_api_key`
- `openrouter_api_key`
- `runway_api_key`
- `azure_openai_api_key`
- `metrics_bearer_token`
- `temporal_db_password`
- Temporal CA, server, internode, and service identity certificate/key pairs

The local-audit Compose overlay additionally requires `local_audit_external_authorization`. It authorizes only a named sandbox run and must never be reused for a release deployment.

## Temporal certificates

Temporal PEM values are retrieved from Key Vault as single-line base64 values, decoded directly into protected files, and never placed in Compose environment values or command arguments. Certificate identities and authorization roles are defined in [Temporal mTLS/authZ staged canary](../ops/temporal-mtls-canary.md).

## Environment-file contract

The protected environment file also supplies bootstrap values consumed directly by upstream images, including `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `SUPABASE_JWT_SECRET`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY`, plus the required application domains. See [Compose VM deployment](../ops/compose-vm-deployment.md) for release-agent behavior.
