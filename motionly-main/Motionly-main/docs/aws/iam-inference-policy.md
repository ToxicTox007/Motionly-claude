# IAM inference user and policy

The Motionly API and the inference workers sign Amazon Bedrock and Amazon S3 requests
with the static access key of a dedicated, narrowly scoped IAM user named
`motionly-inference`. No other principal receives these keys, and the keys are
delivered to only the containers that call inference (see
[Runtime secrets](../security/secrets.md) and
[Compose VM deployment](../ops/compose-vm-deployment.md)).

This document explains the policy in [`iam-inference-policy.json`](iam-inference-policy.json),
the AWS actions it grants, how to validate it, and how to create and rotate the user's
access key without ever printing the secret.

All identifiers in the policy are placeholders. Replace them before you apply the policy:

| Placeholder             | Meaning                                                                                             |
| ----------------------- | --------------------------------------------------------------------------------------------------- |
| `<ACCOUNT_ID>`          | Your 12-digit AWS account id.                                                                       |
| `<STAGING_BUCKET>`      | The ap-south-1 staging bucket (see [S3 buckets](s3-buckets.md)).                                    |
| `<VIDEO_OUTPUT_BUCKET>` | The us-west-2 video output bucket.                                                                  |
| `<BDA_PROJECT_ID>`      | The Bedrock Data Automation project id (see [Data Automation project](data-automation-project.md)). |

## Per-statement rationale

| Sid                                              | Action                                            | Resource                                                                                                                                          | Why                                                                                                                                                                                                                                                                                                        |
| ------------------------------------------------ | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `InvokeNovaInferenceProfilesApSouth1`            | `bedrock:InvokeModel`                             | `inference-profile/apac.amazon.nova-pro-v1:0` and `apac.amazon.nova-lite-v1:0` in ap-south-1                                                      | Planning (`planning.chat`) uses Nova Pro and vision (`vision.object_detection`, `vision.caption`) uses Nova Lite, both through the APAC cross-region inference profile. Converse is authorized by `bedrock:InvokeModel` (F1).                                                                              |
| `InvokeNovaFoundationModelsInDestinationRegions` | `bedrock:InvokeModel`                             | `foundation-model/amazon.nova-pro-v1:0` and `amazon.nova-lite-v1:0` (empty-account form) in the six destination regions                           | A cross-region inference profile must also grant the foundation-model ARN in every destination region (F2). The `bedrock:InferenceProfileArn` condition restricts this to calls routed through the two APAC profiles.                                                                                      |
| `InvokeTitanEmbeddingsApSouth1`                  | `bedrock:InvokeModel`                             | `foundation-model/amazon.titan-embed-text-v2:0` in ap-south-1                                                                                     | Embeddings (`embeddings.text`) call Titan Text Embeddings V2 with InvokeModel. Titan V2 is in-region in ap-south-1, so no profile and no destination-region models are needed (F4).                                                                                                                        |
| `InvokeLumaRayVideoUsWest2`                      | `bedrock:InvokeModel`                             | `foundation-model/luma.ray-v2:0` in us-west-2                                                                                                     | Video (`video.generation.submit`) starts an async invoke of Luma Ray 2. StartAsyncInvoke is authorized by `bedrock:InvokeModel`; there is no `bedrock:StartAsyncInvoke` action (F1). Luma Ray 2 is in-region in us-west-2 only (F8).                                                                       |
| `ReadAsyncInvokeStatusUsWest2`                   | `bedrock:GetAsyncInvoke`                          | `async-invoke/*` in us-west-2                                                                                                                     | The video route polls the async invocation for completion with GetAsyncInvoke (F11).                                                                                                                                                                                                                       |
| `InvokeDataAutomationApacProfileAndProject`      | `bedrock:InvokeDataAutomationAsync`               | `data-automation-profile/apac.data-automation-v1` in the eight destination regions, plus `data-automation-project/<BDA_PROJECT_ID>` in ap-south-1 | Transcription (`transcription.speech_to_text`) and diarization (`audio.diarization`) submit Bedrock Data Automation jobs. BDA requires cross-region inference and AWS's example policy grants the profile ARN in every destination region (F5). The project ARN is included because the job references it. |
| `ReadDataAutomationStatusApSouth1`               | `bedrock:GetDataAutomationStatus`                 | `data-automation-invocation/*` in ap-south-1                                                                                                      | The BDA route polls each invocation for completion with GetDataAutomationStatus (F11).                                                                                                                                                                                                                     |
| `StagingBucketObjectAccessBdaPrefix`             | `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` | `<STAGING_BUCKET>/bda/*`                                                                                                                          | The runner uploads BDA input (`bda/input/<token>.<ext>`), reads BDA output (`bda/output/<token>/...`), and deletes staging objects (cleanup). Bedrock features without a service role use the caller's identity-based S3 permissions (F10).                                                                |
| `StagingBucketListBdaPrefix`                     | `s3:ListBucket`                                   | `<STAGING_BUCKET>` with `s3:prefix` `bda/*`                                                                                                       | Listing the BDA output prefix to collect result objects. Scoped to the `bda/` prefix.                                                                                                                                                                                                                      |
| `VideoOutputBucketObjectAccessVideoPrefix`       | `s3:PutObject`, `s3:GetObject`                    | `<VIDEO_OUTPUT_BUCKET>/video/*`                                                                                                                   | Bedrock writes the generated video to S3 on your behalf (F7 pattern) and the route reads it back. The output bucket must be in the same region as the Bedrock call, us-west-2 (F8). No delete: the lifecycle rule expires `video/` objects after 7 days.                                                   |
| `VideoOutputBucketListVideoPrefix`               | `s3:ListBucket`                                   | `<VIDEO_OUTPUT_BUCKET>` with `s3:prefix` `video/*`                                                                                                | Listing the video output prefix. Scoped to the `video/` prefix.                                                                                                                                                                                                                                            |

The policy uses no `*` in any `Resource` and no wildcards other than the object-key and
invocation-id suffixes (`.../bda/*`, `.../video/*`, `async-invoke/*`,
`data-automation-invocation/*`), which are the narrowest form AWS supports for those
resource types.

## Verified action table

| Action                                                             | Authorizes                                                           | Source                                                                                                                                                                                     |
| ------------------------------------------------------------------ | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `bedrock:InvokeModel`                                              | Converse, InvokeModel, and StartAsyncInvoke                          | <https://docs.aws.amazon.com/bedrock/latest/userguide/inference-prereq.html>, <https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_StartAsyncInvoke.html>                  |
| `bedrock:GetAsyncInvoke`                                           | Reading async-invoke status                                          | <https://docs.aws.amazon.com/cli/latest/reference/bedrock-runtime/get-async-invoke.html>                                                                                                   |
| `bedrock:InvokeDataAutomationAsync`                                | Submitting a BDA job                                                 | <https://docs.aws.amazon.com/bedrock/latest/userguide/bda-cris.html>, <https://docs.aws.amazon.com/cli/latest/reference/bedrock-data-automation-runtime/invoke-data-automation-async.html> |
| `bedrock:GetDataAutomationStatus`                                  | Reading BDA job status                                               | <https://docs.aws.amazon.com/cli/latest/reference/bedrock-data-automation-runtime/get-data-automation-status.html>                                                                         |
| `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:ListBucket` | S3 object and listing access used by Bedrock features and the runner | <https://docs.aws.amazon.com/bedrock/latest/userguide/s3-bucket-access.html>                                                                                                               |

The existence of the Bedrock actions and the absence of `bedrock:StartAsyncInvoke` were
confirmed against the AWS policy generator action list
(<https://awspolicygen.s3.amazonaws.com/js/policies.js>): `bedrock:InvokeModel`,
`InvokeDataAutomationAsync`, `GetDataAutomationStatus`, `GetAsyncInvoke`, and
`ListAsyncInvokes` exist; `StartAsyncInvoke` does not (F1).

## Missing or extra permissions found

- **No `bedrock:StartAsyncInvoke` action exists.** The async video submission is
  authorized by `bedrock:InvokeModel` (F1).
- **`bedrock:InvokeModel` covers Converse.** A separate Converse action is not required (F1).
- **A cross-region inference profile needs the foundation models in every destination
  region.** Granting only the profile ARN is not enough; the destination-region
  foundation-model ARNs (empty-account form) are required (F2).
- **BDA needs the profile ARN in every destination region.** BDA requires cross-region
  inference and AWS's example policy grants the APAC profile ARN in all eight destination
  regions (F5).
- **`bedrock:ListAsyncInvokes` and `bedrock:InvokeModelWithResponseStream` are not needed**
  by Motionly. The video route reads a single invocation by ARN and no route streams
  responses, so these actions are omitted.
- **Marketplace permissions are not granted.** Luma Ray 2 is an AWS Marketplace model and
  the first-time enablement needs `aws-marketplace:Subscribe`, `aws-marketplace:Unsubscribe`,
  and `aws-marketplace:ViewSubscriptions` (F9). Have an administrator perform the first Luma
  Ray 2 invocation so the `motionly-inference` user never needs Marketplace permissions
  (F9). See [Model access](model-access.md).
- **KMS actions are not granted.** The buckets use SSE-S3 (`AES256`), not SSE-KMS, so no
  `kms:*` permission is required. Add `kms:GenerateDataKey` and `kms:Decrypt` only if you
  switch a bucket to SSE-KMS.

### UNVERIFIED - confirm in AWS console

- Resource-level scoping for `bedrock:GetAsyncInvoke` (`async-invoke/*`) and
  `bedrock:GetDataAutomationStatus` (`data-automation-invocation/*`): the Service
  Authorization Reference page could not be fetched this session. If Access Analyzer or a
  live call reports these resource types are not supported at that granularity, widen the
  resource to the account/region or use `*` for only those two read actions and re-run the
  Access Analyzer check.
- Whether `bedrock:InvokeDataAutomationAsync` also evaluates the project ARN in addition to
  the profile ARN. The project ARN is included to be safe; remove it only if Access Analyzer
  flags it as unused.

## Validate the policy with IAM Access Analyzer

```bash
aws accessanalyzer validate-policy \
  --policy-type IDENTITY_POLICY \
  --policy-document file://docs/aws/iam-inference-policy.json
```

The `--policy-type IDENTITY_POLICY` and `--policy-document file://...` flags are confirmed
against the AWS CLI reference
(<https://docs.aws.amazon.com/cli/latest/reference/accessanalyzer/validate-policy.html>).
Review every finding. `ERROR` and `SECURITY_WARNING` findings must be resolved; `SUGGESTION`
and `WARNING` findings should be reviewed. Pay particular attention to any finding about the
resource-level scoping noted as UNVERIFIED above.

## Create the user, policy, and access key without printing the secret

Replace the placeholders in `iam-inference-policy.json` first. Then:

```bash
# 1. Create the user (no console password, no keys yet).
aws iam create-user --user-name motionly-inference

# 2. Create a customer-managed policy from the reviewed document.
aws iam create-policy \
  --policy-name motionly-inference \
  --policy-document file://docs/aws/iam-inference-policy.json

# 3. Attach it to the user (substitute the ARN returned by create-policy).
aws iam attach-user-policy \
  --user-name motionly-inference \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/motionly-inference
```

Create the access key and store both halves directly in SSM SecureString parameters without
echoing the secret to the terminal or to shell history. Write the CLI output to a file with
restrictive permissions, build the SSM input from it, then shred the file:

```bash
umask 077
tmp="$(mktemp)"
# Capture the key material to a 0600 temp file, never to stdout.
aws iam create-access-key --user-name motionly-inference --output json > "$tmp"

# Build SSM put-parameter inputs from the temp file (no secret on the command line).
AKID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["AccessKey"]["AccessKeyId"])' "$tmp")"

# Access key id (not secret, but kept out of history for consistency).
printf '{"Name":"/motionly/<environment>/aws-inference-access-key-id","Type":"SecureString","Value":"%s","Overwrite":true}' "$AKID" > "$tmp.akid.json"
aws ssm put-parameter --cli-input-json file://"$tmp.akid.json" --region <region>

# Secret access key: pipe through a small helper so the value never appears on a command line.
python3 - "$tmp" > "$tmp.secret.json" <<'PY'
import json, sys
k = json.load(open(sys.argv[1]))["AccessKey"]["SecretAccessKey"]
print(json.dumps({
    "Name": "/motionly/<environment>/aws-inference-secret-access-key",
    "Type": "SecureString",
    "Value": k,
    "Overwrite": True,
}))
PY
aws ssm put-parameter --cli-input-json file://"$tmp.secret.json" --region <region>

# Destroy the plaintext copies.
shred -u "$tmp" "$tmp.akid.json" "$tmp.secret.json" 2>/dev/null || rm -f "$tmp" "$tmp.akid.json" "$tmp.secret.json"
```

The `ssm put-parameter` flags `--name`/`--value`/`--type SecureString`/`--overwrite` and the
`--cli-input-json file://...` form are confirmed against the AWS CLI reference
(<https://docs.aws.amazon.com/cli/latest/reference/ssm/put-parameter.html>). Using
`--cli-input-json file://...` keeps the secret out of the process argument list and out of
shell history. Parameter names `/motionly/<environment>/aws-inference-access-key-id` and
`/motionly/<environment>/aws-inference-secret-access-key` match the release agent's optional
fetch (see [Compose VM deployment](../ops/compose-vm-deployment.md)); substitute
`<environment>` and `<region>` for your deployment.

> UNVERIFIED - confirm in AWS console: whether your deployment uses `--key-id` for a
> customer-managed KMS key on the SecureString parameters. If so, add `--key-id <kms-key-id>`
> and grant the inference user `kms:Decrypt` on that key.

## 90-day key rotation

IAM allows at most two access keys per user, which makes zero-downtime rotation possible
(F13, <https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html>):

1. Create the second access key (`aws iam create-access-key --user-name motionly-inference`)
   and store it in the two SSM parameters exactly as above (`--overwrite`).
2. Redeploy so the containers read the new key material (the release agent re-fetches the
   optional SSM parameters).
3. Deactivate the old key: `aws iam update-access-key --user-name motionly-inference
--access-key-id <OLD_KEY_ID> --status Inactive`.
4. Confirm the old key is unused:
   `aws iam get-access-key-last-used --access-key-id <OLD_KEY_ID>` and watch application
   health for at least one cache window (the adapter re-reads the key files at most every
   300 s).
5. Delete the old key: `aws iam delete-access-key --user-name motionly-inference
--access-key-id <OLD_KEY_ID>`.

Rotate on a 90-day cadence (or immediately on suspected compromise). The emergency stop is to
deactivate the active key, which halts all Bedrock and S3 access for the inference user; see
`cost-guardrails.md` (added by the cost-guardrails stage).

## Later: upgrade to IAM Roles Anywhere

Static keys are the chosen starting point. To remove long-lived secrets entirely, migrate to
IAM Roles Anywhere, which issues temporary credentials to workloads outside AWS using X.509
certificates, trust anchors, profiles, and roles (F13,
<https://docs.aws.amazon.com/rolesanywhere/latest/userguide/introduction.html>). The Bedrock
adapter would then read temporary credentials (access key id, secret access key, and session
token) from the Roles Anywhere credential helper instead of the two static-key files, and the
two SSM SecureString parameters would be retired. This is a future change and is not part of
the current deployment.
