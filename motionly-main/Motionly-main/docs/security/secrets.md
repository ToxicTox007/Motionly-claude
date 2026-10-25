# Runtime secrets

The release agent on the AWS instance (`scripts/deploy/apply_release.sh`) reads secrets from SSM Parameter Store under `/motionly/<environment>/` and creates a release-specific `SECRETS_DIR` with mode `0700` and writes each secret file read-only with mode `0444`. The directory mode keeps other host users out; the file mode lets non-root container users read their mounts, because Compose mounts file-based secrets with the host file mode and ignores `uid`, `gid` and `mode`. Values come from SSM Parameter Store through the instance role and never pass through command arguments.

The release process must never commit a local secret directory. Application containers read credentials from read-only `/run/secrets/*` mounts.

## Secret files

Create or retrieve these file names before starting `infra/docker-compose.gpu.yml`:

- `postgres_password`
- `redis_password`
- `supabase_service_role_key`
- `supabase_worker_key`: a Supabase JWT whose `role` claim is `motionly_worker` — the restricted database role (migration `202610190501_task18_restricted_worker_role.sql`) that PostgREST switches into for worker requests. It is mounted ONLY into the media worker services (never the API or web) so a compromised worker image cannot reach the `auth` schema or storage buckets outside the four workers use. Workers read it as `SUPABASE_SERVICE_ROLE_KEY_FILE`; it is NOT the service-role key.
- `supabase_jwt_secret`
- `realtime_db_enc_key`: Supabase Realtime's 16-byte key for encrypting tenant settings it stores in Postgres
- `realtime_secret_key_base`: Realtime's Phoenix endpoint secret
- `realtime_metrics_jwt_secret`: signs the tokens Realtime's own `/metrics` endpoint accepts; the pinned release refuses to start without it
- `admin_api_token`
- `worker_api_token`
- `ai_secrets_bundle`: one JSON object holding every inference route credential. Registry routes reference keys in it as `bundle:<key>` (see the [registry guide](../ai-registry/README.md)). The release agent reads it from the `ai-secrets-bundle` parameter; when that parameter is missing it writes an empty file, which suits a registry that uses only `file:` references.
- `metrics_bearer_token`
- `temporal_db_password`
- `edge_origin_verify`: the value CloudFront sends in `X-Motionly-Origin-Verify` (32–256 characters of `A-Z a-z 0-9 _ -`). The ingress refuses to start without a valid value and refuses every request that does not carry it.
- `edge_origin_verify_previous`: empty except during a rotation, when it holds the old value so requests from edge locations that have not picked up the new one are still accepted
- Temporal CA, server, internode, and service identity certificate/key pairs

To rotate the origin secret: write the old value to `edge-origin-verify-previous` and a new value to `edge-origin-verify`, release (the ingress now accepts both), update the CloudFront custom header, wait for the distribution to deploy, then empty `edge-origin-verify-previous` and release again.

The local-audit Compose overlay additionally requires `local_audit_external_authorization`. It authorizes only a named sandbox run and must never be reused for a release deployment.

## Inference secrets and redaction

Inference credentials are resolved from registry secret references at start-up (`file:` under `AI_SECRETS_DIR`, default `/run/secrets`, or `bundle:` from `AI_SECRETS_BUNDLE_FILE`). Every resolved value, and every OAuth access token obtained with it, is registered with the log redactor, which replaces the exact value in log messages, structured fields and exception text in both the API and the Node worker. No credential is passed to ffmpeg, Chromium or other media subprocesses; their environments are allowlists.

Operators upgrading from a release with per-vendor keys remove these old secret parameters and files once the new release is healthy: `groq-api-key`, `openrouter-api-key`, `runway-api-key`, `azure-openai-api-key` and `jina-api-key` (files `groq_api_key`, `openrouter_api_key`, `runway_api_key`, `azure_openai_api_key`, `jina_api_key`). The object-detection, diarization and stem-separation adapters were removed in the provider-neutral release (plan Task 19 item 2); remove their key parameters and files too: `lalal-license-key`, `pyannoteai-api-key` and `google-vision-api-key` (files `lalal_license_key`, `pyannoteai_api_key`, `google_vision_api_key`). These capabilities now use registry routes whose credentials live in the `ai-secrets-bundle` parameter (`ai_secrets_bundle`).

## Temporal certificates

Temporal PEM values are retrieved from SSM Parameter Store as single-line base64 values, decoded directly into protected files, and never placed in Compose environment values or command arguments. Certificate identities and authorization roles are defined in [Temporal mTLS/authZ staged canary](../ops/temporal-mtls-canary.md).

## Environment-file contract

The protected environment file also supplies bootstrap values consumed directly by upstream images, including `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `SUPABASE_JWT_SECRET`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY`, plus the required application domains. See [Compose VM deployment](../ops/compose-vm-deployment.md) for release-agent behavior.
