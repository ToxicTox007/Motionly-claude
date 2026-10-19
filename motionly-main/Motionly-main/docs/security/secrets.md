# Runtime secrets

The release agent on the AWS instance (`scripts/deploy/apply_release.sh`) reads secrets from SSM Parameter Store under `/motionly/<environment>/` and creates a release-specific `SECRETS_DIR` with mode `0700` and writes each secret file read-only with mode `0444`. The directory mode keeps other host users out; the file mode lets non-root container users read their mounts, because Compose mounts file-based secrets with the host file mode and ignores `uid`, `gid` and `mode`. Values come from SSM Parameter Store through the instance role and never pass through command arguments.

The release process must never commit a local secret directory. Application containers read credentials from read-only `/run/secrets/*` mounts.

## Secret files

Create or retrieve these file names before starting `infra/docker-compose.gpu.yml`:

- `postgres_password`
- `redis_password`
- `supabase_service_role_key`
- `supabase_jwt_secret`
- `admin_api_token`
- `worker_api_token`
- `groq_api_key`
- `openrouter_api_key`
- `runway_api_key`
- `azure_openai_api_key`
- `metrics_bearer_token`
- `temporal_db_password`
- `edge_origin_verify`: the value CloudFront sends in `X-Motionly-Origin-Verify` (32–256 characters of `A-Z a-z 0-9 _ -`). The ingress refuses to start without a valid value and refuses every request that does not carry it.
- `edge_origin_verify_previous`: empty except during a rotation, when it holds the old value so requests from edge locations that have not picked up the new one are still accepted
- Temporal CA, server, internode, and service identity certificate/key pairs

To rotate the origin secret: write the old value to `edge-origin-verify-previous` and a new value to `edge-origin-verify`, release (the ingress now accepts both), update the CloudFront custom header, wait for the distribution to deploy, then empty `edge-origin-verify-previous` and release again.

The local-audit Compose overlay additionally requires `local_audit_external_authorization`. It authorizes only a named sandbox run and must never be reused for a release deployment.

## Temporal certificates

Temporal PEM values are retrieved from SSM Parameter Store as single-line base64 values, decoded directly into protected files, and never placed in Compose environment values or command arguments. Certificate identities and authorization roles are defined in [Temporal mTLS/authZ staged canary](../ops/temporal-mtls-canary.md).

## Environment-file contract

The protected environment file also supplies bootstrap values consumed directly by upstream images, including `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `SUPABASE_JWT_SECRET`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY`, plus the required application domains. See [Compose VM deployment](../ops/compose-vm-deployment.md) for release-agent behavior.
