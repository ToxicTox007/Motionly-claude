# Local development environment

The repository root is `motionly-main/Motionly-main`. The setup scripts create `infra/.env.local` and the local secret-file placeholders used by Docker Compose.

## Prerequisites

- Docker Desktop or Docker Engine with Compose
- Bash for `setup-local-env.sh`, or PowerShell for `setup-local-env.ps1`
- Node.js and pnpm for application commands
- OpenSSL when generating local certificates or running certificate-dependent checks

## Initialize local configuration

From the repository root, run one of:

```bash
bash setup-local-env.sh
```

```powershell
.\setup-local-env.ps1
```

The scripts create local-only files under `infra/`. Never commit `infra/.env.local`, populated secret files, certificates, or generated credentials.

For the secret-file contract, see [Runtime secrets](../security/secrets.md). For the production topology and health checks, see [Operations](../operations.md).

## Start the minimal stack

```bash
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.dev.yml \
  up -d postgres redis temporal-postgres
```

On PowerShell, use the same command on one line or use PowerShell's backtick line continuation.

## Start the full development stack

```bash
docker compose --env-file infra/.env.local \
  -f infra/docker-compose.gpu.yml \
  -f infra/docker-compose.dev.yml \
  up -d --build
```

Inspect service status with:

```bash
docker compose --env-file infra/.env.local -f infra/docker-compose.gpu.yml ps
```

## Troubleshooting

- If Compose reports a missing variable, rerun the setup script and inspect `infra/.env.local`.
- If a secret file is missing, verify `SECRETS_DIR` and the file names in [Runtime secrets](../security/secrets.md).
- If a service cannot connect, start PostgreSQL, Redis, and Temporal PostgreSQL before application services.
- If Temporal certificate setup is required, follow [Temporal mTLS canary](../ops/temporal-mtls-canary.md) and use only local, ignored certificate files.
