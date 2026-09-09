# Runtime secrets

The single-VM release agent creates a release-specific `SECRETS_DIR` with mode `0700`
and writes every file below with mode `0600`. It obtains their values from Azure Key
Vault using the VM managed identity; neither Compose files nor GitHub Actions command
arguments carry secret values. Do not commit a local secret directory.

Create or retrieve these file names before starting `infra/docker-compose.gpu.yml`:

- `postgres_password`
- `redis_password`
- `supabase_service_role_key`
- `supabase_jwt_secret`
- `admin_api_token` (admin endpoints only)
- `worker_api_token` (internal worker endpoints only)
- `publish_token_encryption_key`
- `publish_oauth_state_secret`
- `groq_api_key`
- `openrouter_api_key`
- `runway_api_key`
- `azure_openai_api_key` (an empty, mode-0600 file when Azure OpenAI is disabled)
- `metrics_bearer_token`
- `temporal_db_password`
- `temporal_ca_cert`, `temporal_server_cert`, `temporal_server_key`, and `temporal_internode_cert` / `temporal_internode_key`
- `temporal_api_cert` / `temporal_api_key`
- `temporal_dispatcher_cert` / `temporal_dispatcher_key`
- `temporal_worker_cert` / `temporal_worker_key`
- `temporal_reconciler_cert` / `temporal_reconciler_key`
- `temporal_namespace_bootstrap_cert` / `temporal_namespace_bootstrap_key`
- `temporal_diagnostics_cert` / `temporal_diagnostics_key`

For `infra/docker-compose.local-audit.yml`, the protected local `SECRETS_DIR` also
must contain `local_audit_external_authorization`. It authorizes a named sandbox
run only; it is mounted as `/run/secrets/local_audit_external_authorization` and
must never be committed or reused for a release deployment.

Temporal PEM files are retrieved from Key Vault as single-line base64 values, decoded directly into these protected files, and never placed in Compose environment values or command arguments. See `infra/temporal/README.md` for required certificate common names.

The protected environment file must also supply the bootstrap values consumed directly
by upstream images: `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `SUPABASE_JWT_SECRET`,
`SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY`. It supplies the required
`APP_DOMAIN`, `API_DOMAIN`, `SUPABASE_DOMAIN`, and `SENTRY_DSN` values as well.
Application containers read their application credentials from read-only
`/run/secrets/*` mounts.
