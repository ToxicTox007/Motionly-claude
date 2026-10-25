# AWS infrastructure

Motionly runs in `ap-south-1` (Mumbai) as one CloudFormation stack: `infra/aws/motionly.template.json`. It is rendered from the edge contract (`infra/aws/edge-contract.json`) by `scripts/aws/render_template.py`, so CloudFront in production and the CloudFront simulator in the CPU stage apply the same rules. Edit the contract or the renderer, then re-render:

```bash
python scripts/aws/render_template.py          # write the template
pnpm verify --stage aws                        # render check, cfn-lint, cfn-guard, tests
```

The `aws` stage is static validation only: it never calls AWS. Live deployment is a later, separate step.

## What the stack creates

- **CloudFront** with the default `*.cloudfront.net` certificate, the behaviours, managed cache and origin-request policies, response-header policy and error caching from the edge contract. It sends `X-Motionly-Origin-Verify` to the origin; the origin's Caddy guard rejects requests without it ([Runtime secrets](../security/secrets.md) describes rotation).
- **One EC2 instance** behind an Elastic IP. `ComputeProfile=gpu` is the production `g6.12xlarge` on the Deep Learning Base OSS NVIDIA Driver AMI (Amazon Linux 2023); `ComputeProfile=cpu` is an `m7i.2xlarge` on Amazon Linux 2023 for rehearsals, where GPU-dependent capabilities are unavailable. `ComputeProfile=cpu` can also run CPU-only production by setting `MOTIONLY_CPU_TOPOLOGY=gpu-free` on the host, which deploys the GPU-free remote overlay so GPU work goes to remote Bedrock routes and CPU work stays local (see [Compose production deployment](compose-vm-deployment.md)). The security group admits only the CloudFront origin-facing prefix list on port 80 and allows HTTPS out; there is no SSH and no key pair. Instance metadata requires IMDSv2 with a hop limit of 1, so containers cannot read the instance's credentials.
- **A persistent data volume** (encrypted gp3, snapshot on deletion) mounted at `/srv/motionly` by `infra/aws/bootstrap.sh`, which also installs Docker and a pinned, checksum-verified Docker Compose plugin. Docker's data root is on this volume.
- **Three S3 buckets** for releases, media and backups: encrypted, versioned, ACLs disabled, all public access blocked, TLS required. Media and backups are retained when the stack is deleted, and the instance cannot delete backups.
- **A GitHub OIDC deploy role** that only workflows in one repository environment can assume. It can upload releases and run `AWS-RunShellScript` on this instance through SSM Run Command, nothing else.

The instance reads its secrets from SSM Parameter Store under `/motionly/<environment>/`; secrets are never in the template. The origin secret is a `NoEcho` parameter whose value the deploy job reads from `/motionly/<environment>/edge/origin-verify`.

## Parameters the deploy supplies

| Parameter                      | Value                                                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `Environment`                  | `production` or `staging`                                                                                     |
| `ComputeProfile`               | `gpu` (production) or `cpu`                                                                                   |
| `CloudFrontOriginPrefixListId` | id of `com.amazonaws.global.cloudfront.origin-facing` in ap-south-1 (`aws ec2 describe-managed-prefix-lists`) |
| `OriginVerifySecret`           | current value of `/motionly/<environment>/edge/origin-verify`                                                 |
| `GitHubRepository`             | `owner/name`                                                                                                  |
| `GitHubOidcProviderArn`        | existing provider ARN, or empty to create one                                                                 |

The `PublicOrigin` output is the release's `PUBLIC_ORIGIN`.

## Releases

Releases are published and applied by `.github/workflows/deploy-aws.yml` and rehearsed locally by the `aws` verify stage ([Compose production deployment](compose-vm-deployment.md)).

## Not covered yet

There is no WAF, access logging or alarms in the stack.
