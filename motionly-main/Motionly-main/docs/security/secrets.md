# Runtime secrets

The release agent on the AWS instance (`scripts/deploy/apply_release.sh`) reads secrets from SSM Parameter Store under `/motionly/<environment>/` and creates a release-specific `SECRETS_DIR` with mode `0700` and writes each secret file read-only with mode `0444`. The directory mode keeps other host users out; the file mode lets non-root container users read their mounts, because Compose mounts file-based secrets with the host file mode and ignores `uid`, `gid` and `mode`. Values come from SSM Parameter Store through the instance role and never pass through command arguments.

The release process must never commit a local secret directory. Application containers read credentials from read-only `/run/secrets/*` mounts.

## Secret files

Create or retrieve these file names before starting `infra/docker-compose.gpu.yml`:

- `postgres_password`: the `postgres` (and Supabase `supabase_admin`) database password. Only the database servers, `db-migrate`, `temporal-db-init` and Realtime receive it; Auth, REST and Storage log in with their own passwords (see [Database credentials and TLS](#database-credentials-and-tls)).
- `redis_password`
- `supabase_service_role_key`
- `supabase_worker_key`: a Supabase JWT whose `role` claim is `motionly_worker` — the restricted database role (migration `202610190501_task18_restricted_worker_role.sql`) that PostgREST switches into for worker requests. It is mounted ONLY into the media worker services (never the API or web) so a compromised worker image cannot reach the `auth` schema or storage buckets outside the four workers use. Workers read it as `SUPABASE_SERVICE_ROLE_KEY_FILE`; it is NOT the service-role key.
- `supabase_jwt_secret`: the HS256 key that signs every Supabase JWT. Mounted only into the services that verify or issue tokens (GoTrue, PostgREST, Storage, Realtime, the API, the Temporal dispatcher/worker and the reconciler). Media workers never receive it: with it a compromised worker could mint a `service_role` token and step around `motionly_worker`. Workers start with `PROCESS_ROLE=worker`, which does not require it.
- `realtime_db_enc_key`: Supabase Realtime's 16-byte key for encrypting tenant settings it stores in Postgres
- `realtime_secret_key_base`: Realtime's Phoenix endpoint secret
- `realtime_metrics_jwt_secret`: signs the tokens Realtime's own `/metrics` endpoint accepts; the pinned release refuses to start without it
- `admin_api_token`
- `worker_api_token`
- `ai_secrets_bundle`: one JSON object holding every inference route credential. Registry routes reference keys in it as `bundle:<key>` (see the [registry guide](../ai-registry/README.md)). The release agent reads it from the `ai-secrets-bundle` parameter; when that parameter is missing it writes an empty file, which suits a registry that uses only `file:` references.
- `aws_inference_access_key_id` and `aws_inference_secret_access_key`: the static access keys of the dedicated, narrowly scoped AWS IAM user that Amazon Bedrock routes authenticate with (see the AWS deployment guide under `docs/aws`). A Bedrock registry adapter references them as `file:aws_inference_access_key_id` / `file:aws_inference_secret_access_key`; they are never exposed as `AWS_*` environment variables. They are mounted ONLY into the API and the worker services that call a Bedrock route (`api`, `provider-worker`, `planning-worker`, `gpu-inference-worker`, and `cpu-vision-worker` under the GPU-free remote overlay); the media (ffmpeg/matte), rendering (remotion), Demucs and every infrastructure service get no AWS credentials. The release agent reads them from the `aws-inference-access-key-id` and `aws-inference-secret-access-key` parameters; both are fetched with `optional`, so a deployment without any Bedrock route leaves the two files empty and still starts.
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

## Database credentials and TLS

Two one-shot Compose services create database credentials on the host itself; there is nothing to provision in SSM (database audit DB-21):

- `db-tls` (`infra/db/generate-tls.sh`) issues an internal database CA (ten years) and a server certificate for `postgres` and `temporal-postgres` (two years, re-issued by the same CA 60 days before it expires). The CA key stays in the `postgres-tls-ca-key` volume, which only `db-tls` mounts; the servers mount `postgres-tls-server`, every client mounts the CA certificate (`postgres-tls-ca`). Both servers accept password logins from other containers only over TLS (`infra/db/pg_hba.conf`, `pg_hba.temporal.conf`), and every client verifies the certificate and host name. The one exception is Supabase Realtime's tenant connection, which its self-hosted seed pins to plain TCP: `supabase_admin` may still log in to the Supabase database without TLS.
- `db-roles` (`infra/db/apply-role-passwords.sh`) gives `supabase_auth_admin`, `authenticator` and `supabase_storage_admin` their own random passwords, kept in the `db-auth-credentials`, `db-rest-credentials` and `db-storage-credentials` volumes. Each volume is mounted read-only into its one service; every release sets the passwords on the roles again. To rotate one, delete its volume and release.

Rolling back to a release from before these services existed would leave its Auth, REST and Storage unable to log in, so the release agent first sets those three roles back to `postgres_password` when the release it activates has no `db-roles` service.

## Environment-file contract

The protected environment file also supplies bootstrap values consumed directly by upstream images, including `REDIS_PASSWORD`, `SUPABASE_JWT_SECRET`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY`, plus the required application domains. It carries no database password. See [Compose VM deployment](../ops/compose-vm-deployment.md) for release-agent behavior.
