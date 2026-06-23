# Runtime secrets

Create these files before starting `infra/docker-compose.gpu.yml`; never commit their values:

- `postgres_password`
- `supabase_service_role_key`
- `supabase_jwt_secret`
- `admin_api_token`
- `publish_token_encryption_key`

The matching bootstrap values (`POSTGRES_PASSWORD`, Supabase JWT keys, and `REDIS_PASSWORD`) must
also be supplied to Docker Compose through the deployment secret manager or a protected environment
file because the upstream Supabase and Redis images consume them during initialization. Application
containers receive sensitive values from read-only `/run/secrets/*` mounts.
