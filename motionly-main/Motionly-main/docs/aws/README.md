# Running Motionly on AWS with Amazon Bedrock

This directory is the operator guide for pointing Motionly's provider-neutral inference
registry at Amazon Bedrock. Motionly runs on an EC2 host with Docker Compose; only the API
and the inference workers receive AWS credentials, delivered as secret files from a dedicated,
narrowly scoped IAM user named `motionly-inference`. See
[Runtime secrets](../security/secrets.md) and
[Compose VM deployment](../ops/compose-vm-deployment.md) for how the credentials are mounted.

Every identifier in these documents and their JSON templates is a placeholder. Nothing here
contains a real account id, ARN, key, bucket name, or email. Replace the placeholders in your
own environment and never commit real values.

## Decisions recorded (2026-10-25)

The operator confirmed the following for the first paid AWS deployment. The code, registry and
runbooks in this directory reflect them.

1. **CPU-only production via the signed release.** A CPU-only production host
   (`ComputeProfile=cpu`, `m7i.2xlarge`) runs `docker-compose.gpu.yml` plus the GPU-free overlay
   `docker-compose.remote-ai.yml` with the `cpu-vision-worker` image and the Demucs worker. It is
   opted in on the host with `MOTIONLY_CPU_TOPOLOGY=gpu-free` and `MOTIONLY_LOCAL_STEMS=enabled`
   (step 8a below); `apply_release.sh` deploys the overlay when a release's signed manifest carries
   `MOTIONLY_CPU_VISION_WORKER_IMAGE` and its bundle carries the overlay.
2. **Video generation uses Luma Ray 2.** `luma.ray-v2:0` in us-west-2, billed through AWS
   Marketplace (see [Model access](model-access.md)).
3. **Cross-Region processing is accepted.** Nova Pro/Lite via the `apac.*` inference profiles may
   process requests and results in ap-northeast-1, ap-northeast-2, ap-northeast-3, ap-south-1,
   ap-southeast-1, ap-southeast-2; Bedrock Data Automation (`apac.data-automation-v1`) may process
   in ap-northeast-1, ap-northeast-2, ap-northeast-3, ap-south-1, ap-south-2, ap-southeast-1,
   ap-southeast-2, ap-southeast-4. For Data Automation, the
   [AWS cross-region documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/bda-cris.html)
   says data stays **stored** only in the source region (ap-south-1) while requests and results may
   move within the geography. For the Nova profiles, AWS states cross-region traffic stays on the AWS
   network, encrypted in transit, and CloudTrail records the processing region
   ([cross-region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)). Titan embeddings stay in ap-south-1; video is processed and stored in
   us-west-2. The registry's `data_processor.purpose` strings disclose this on the public privacy
   page.

## Order of operations

Perform these steps in order. Later steps depend on identifiers produced by earlier ones.

1. **IAM user and policy.** Create the `motionly-inference` user and attach the scoped policy.
   See [IAM inference user and policy](iam-inference-policy.md) and
   [`iam-inference-policy.json`](iam-inference-policy.json). Validate with IAM Access
   Analyzer.
2. **S3 buckets.** Create the ap-south-1 staging bucket and the us-west-2 video output bucket,
   lock them down, and apply the lifecycle rules. See [S3 buckets](s3-buckets.md).
3. **Bedrock Data Automation project.** Create the ap-south-1 BDA project for transcription
   and diarization and read its project ARN. See
   [Data Automation project](data-automation-project.md).
4. **Model access.** Confirm Nova Pro, Nova Lite, Titan Embeddings V2, BDA, and Luma Ray 2 are
   reachable, and have an administrator perform the first Luma Ray 2 invocation. See
   [Model access and smoke tests](model-access.md).
5. **SSM secrets.** Store the inference user's access key id and secret access key in the two
   SSM SecureString parameters `/motionly/<environment>/aws-inference-access-key-id` and
   `/motionly/<environment>/aws-inference-secret-access-key` (see the IAM document and
   [Compose VM deployment](../ops/compose-vm-deployment.md)).
6. **Registry fill and preflight.** Fill the placeholders and pricing markers in
   `infra/ai-registry/registry.json`, then run the preflight CLI
   (`python -m app.ops.preflight_inference_registry`) in production mode. (Registry and
   preflight are owned by other stages.)
7. **Budgets.** Create an account-wide monthly AWS Budget with alert notifications. See
   `cost-guardrails.md` (added by the cost-guardrails stage).
8. **First paid test.** Run one short clip and one 5 s video end to end and compare actual
   cost with the registry prediction. See `first-paid-test.md` (added by the runbook stage).

For CPU-only production (the decision above), set the two optional, non-secret topology settings in
`/etc/motionly/host.env` before the first paid test, alongside `MOTIONLY_COMPUTE_PROFILE=cpu`:
`MOTIONLY_CPU_TOPOLOGY=gpu-free` and `MOTIONLY_LOCAL_STEMS=enabled`
(`infra/compose-host/host.env.example`). Both default to today's behaviour when unset, so an
existing host is unaffected until it opts in. Preflight then reports the resolved topology in its
success line (see `first-paid-test.md` section 2 and
[Compose VM deployment](../ops/compose-vm-deployment.md)).

## Documents in this directory

- [IAM inference user and policy](iam-inference-policy.md) —
  [`iam-inference-policy.json`](iam-inference-policy.json)
- [S3 buckets](s3-buckets.md) — [`s3-staging-bucket-policy.json`](s3-staging-bucket-policy.json),
  [`s3-video-output-bucket-policy.json`](s3-video-output-bucket-policy.json),
  [`s3-staging-lifecycle.json`](s3-staging-lifecycle.json),
  [`s3-video-output-lifecycle.json`](s3-video-output-lifecycle.json)
- [Model access and smoke tests](model-access.md)
- [Data Automation project](data-automation-project.md) —
  [`bda-audio-standard-output.json`](bda-audio-standard-output.json)

Cost guardrails (`cost-guardrails.md`) and the first-paid-test runbook
(`first-paid-test.md`) are added by later stages.

## Placeholder legend

| Placeholder                    | Meaning                                                                                    |
| ------------------------------ | ------------------------------------------------------------------------------------------ |
| `<ACCOUNT_ID>`                 | Your 12-digit AWS account id.                                                              |
| `<STAGING_BUCKET>`             | Staging bucket name (ap-south-1), prefix `bda/`.                                           |
| `<VIDEO_OUTPUT_BUCKET>`        | Video output bucket name (us-west-2), prefix `video/`.                                     |
| `<BDA_PROJECT_ID>`             | Bedrock Data Automation project id.                                                        |
| `<BREAK_GLASS_ROLE_NAME>`      | Name of the IAM role allowed through the bucket policy for recovery.                       |
| `<ALERT_EMAIL>`                | Email that receives budget alerts (used by the cost-guardrails stage).                     |
| `<MONTHLY_BUDGET_USD>`         | Monthly budget amount in USD (suggested starting value: 10).                               |
| `motionly-inference`           | The IAM user name (not a secret).                                                          |
| `"FILL FROM AWS PRICING PAGE"` | Marker in registry number fields to replace with the real price from the AWS pricing page. |

`<environment>` and `<region>` in command examples are your deployment's environment name and
AWS region.

## Regions and models

| Capability                                                                  | Model / profile                | Region                | API                      |
| --------------------------------------------------------------------------- | ------------------------------ | --------------------- | ------------------------ |
| `planning.chat`                                                             | `apac.amazon.nova-pro-v1:0`    | ap-south-1 (APAC geo) | Converse                 |
| `vision.object_detection`, `vision.caption`                                 | `apac.amazon.nova-lite-v1:0`   | ap-south-1 (APAC geo) | Converse                 |
| `embeddings.text`                                                           | `amazon.titan-embed-text-v2:0` | ap-south-1            | InvokeModel              |
| `transcription.speech_to_text`, `audio.diarization`                         | BDA `apac.data-automation-v1`  | ap-south-1 (APAC geo) | Bedrock Data Automation  |
| `video.generation.submit`                                                   | `luma.ray-v2:0`                | us-west-2             | StartAsyncInvoke (async) |
| `matte.segmentation`, `audio.sound_classification`, `audio.stem_separation` | local CPU                      | local                 | n/a                      |

## Unverified facts

The following could not be confirmed from an official AWS page this session and are marked
inline wherever they are used. Confirm each in the AWS console or with a live call before
relying on it:

- Resource-level scoping for `bedrock:GetAsyncInvoke` (`async-invoke/*`) and
  `bedrock:GetDataAutomationStatus` (`data-automation-invocation/*`). The Service
  Authorization Reference page could not be fetched. Validate the IAM policy with Access
  Analyzer.
- Whether `bedrock:InvokeDataAutomationAsync` also evaluates the project ARN in addition to
  the profile ARN.
- Whether BDA and async invoke S3 access carry the caller's `aws:PrincipalArn` through a
  bucket-policy `Deny` (F10). Test with the end-to-end smoke tests.
- The SigV4 canonical path encoding for model ids and ARNs containing `:` (tested against AWS
  test vectors, not a live call), including the `bedrock-runtime` model-id path encoding for
  `luma.ray-v2:0`.
- Whether `generativeField` can be omitted or set to `DISABLED` on the BDA project without a
  `types` list.
- The Budgets global endpoint region (expected us-east-1).
- Whether your deployment encrypts the SSM SecureString parameters with a customer-managed KMS
  key (the `--key-id` flag). If so, the inference user also needs `kms:Decrypt` on that key.
- All Bedrock and AWS Marketplace pricing values (registry pricing fields carry the
  `FILL FROM AWS PRICING PAGE` marker).
- The exact `start-async-invoke`, `invoke-data-automation-async`, and
  `put-public-access-block` option/body shapes noted inline; the top-level command and flags
  are confirmed but inner JSON keys should be re-checked against your CLI version.
- The exact `aws budgets create-budget` request body: the top-level flags (`--account-id`,
  `--budget`, `--notifications-with-subscribers`) and the `BudgetType`/`TimeUnit`/
  `NotificationType`/`ComparisonOperator`/`ThresholdType`/at-most-5-notifications constraints are
  confirmed from the create-budget CLI reference; the exact `CostTypes` field set in
  [`budget.json`](budget.json) should be re-checked against your CLI version.
- Whether an account-wide `COST` budget with no `CostFilters` captures AWS Marketplace / Luma Ray 2
  charges in the same alert as Bedrock charges. The budget is account-wide precisely so it should,
  and it is alert-only regardless, but confirm the first alert includes Marketplace spend.
- `GET /health/dependencies` surfacing `route_hard_cap_exhausted` and per-route circuit state is
  verified in code (`apps/api/app/services/model_gateway.py`), but the exact JSON shape of that
  response was not re-generated this session.
