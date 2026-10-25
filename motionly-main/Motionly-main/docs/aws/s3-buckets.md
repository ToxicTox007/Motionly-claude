# S3 buckets for Bedrock inference

Motionly uses two S3 buckets with Amazon Bedrock:

| Bucket       | Placeholder             | Region     | Prefix   | Lifecycle                            | Used by                                                           |
| ------------ | ----------------------- | ---------- | -------- | ------------------------------------ | ----------------------------------------------------------------- |
| Staging      | `<STAGING_BUCKET>`      | ap-south-1 | `bda/`   | expire `bda/` objects after 1 day    | Bedrock Data Automation input/output (transcription, diarization) |
| Video output | `<VIDEO_OUTPUT_BUCKET>` | us-west-2  | `video/` | expire `video/` objects after 7 days | Luma Ray 2 async video generation output                          |

The runner writes BDA input to `bda/input/<token>.<ext>` and BDA writes its output to
`bda/output/<token>/...`. Bedrock writes the generated video under `video/`.

> Why us-west-2 for video, not us-east-1? An earlier plan placed the video bucket in
> us-east-1 for Nova Reel 1.1. Nova Reel reached end of life on 2026-09-30 and now fails with
> `ResourceNotFoundException` (F7,
> <https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-reel.html>).
> Motionly uses Luma Ray 2 instead, which is in-region in us-west-2 only, and the async
> output bucket must be in the same region as the Bedrock call (F8,
> <https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-luma-ai-ray2.html>). The
> video bucket is therefore in us-west-2.

Both buckets are private, TLS-only, locked to the `motionly-inference` user and a
break-glass role, encrypted with SSE-S3, and have versioning disabled.

## Checklist

For each bucket:

- [ ] Create the bucket in the correct region.
- [ ] Enable S3 Block Public Access (all four settings).
- [ ] Set default encryption to SSE-S3 (`AES256`) with `BucketKeyEnabled`.
- [ ] Set object ownership to `BucketOwnerEnforced` (ACLs disabled).
- [ ] Apply the lifecycle rule (staging `bda/` 1 day, video `video/` 7 days; both abort
      incomplete multipart uploads after 1 day).
- [ ] Leave versioning disabled.
- [ ] Apply the restrictive bucket policy (deny all principals except the inference user and
      break-glass role; deny non-TLS).

## Create the buckets

`create-bucket` in us-east-1 takes no `LocationConstraint`; every other region requires
`--create-bucket-configuration LocationConstraint=<region>` (F11,
<https://docs.aws.amazon.com/cli/latest/reference/s3api/create-bucket.html>). Neither of
these buckets is in us-east-1, so both need the constraint. `--object-ownership
BucketOwnerEnforced` disables ACLs.

```bash
# Staging bucket (ap-south-1)
aws s3api create-bucket \
  --bucket <STAGING_BUCKET> \
  --region ap-south-1 \
  --create-bucket-configuration LocationConstraint=ap-south-1 \
  --object-ownership BucketOwnerEnforced

# Video output bucket (us-west-2)
aws s3api create-bucket \
  --bucket <VIDEO_OUTPUT_BUCKET> \
  --region us-west-2 \
  --create-bucket-configuration LocationConstraint=us-west-2 \
  --object-ownership BucketOwnerEnforced
```

## Block public access

```bash
aws s3api put-public-access-block \
  --bucket <STAGING_BUCKET> \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-public-access-block \
  --bucket <VIDEO_OUTPUT_BUCKET> \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

> UNVERIFIED - confirm in AWS console: the exact shorthand for
> `--public-access-block-configuration`. Re-check against
> <https://docs.aws.amazon.com/cli/latest/reference/s3api/put-public-access-block.html>
> before running; all four flags should be `true`.

## Default encryption (SSE-S3 with Bucket Key)

`--server-side-encryption-configuration` is confirmed against the AWS CLI reference
(<https://docs.aws.amazon.com/cli/latest/reference/s3api/put-bucket-encryption.html>). Use
`AES256` (SSE-S3); `BucketKeyEnabled` reduces request cost.

```bash
aws s3api put-bucket-encryption \
  --bucket <STAGING_BUCKET> \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'

aws s3api put-bucket-encryption \
  --bucket <VIDEO_OUTPUT_BUCKET> \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
```

## Lifecycle rules

Apply the shipped lifecycle documents. `put-bucket-lifecycle-configuration --bucket
--lifecycle-configuration` is confirmed against the AWS CLI reference
(<https://docs.aws.amazon.com/cli/latest/reference/s3api/put-bucket-lifecycle-configuration.html>).
The staging rule expires `bda/` objects after 1 day; the video rule expires `video/` objects
after 7 days; both abort incomplete multipart uploads after 1 day.

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket <STAGING_BUCKET> \
  --lifecycle-configuration file://docs/aws/s3-staging-lifecycle.json

aws s3api put-bucket-lifecycle-configuration \
  --bucket <VIDEO_OUTPUT_BUCKET> \
  --lifecycle-configuration file://docs/aws/s3-video-output-lifecycle.json
```

See [`s3-staging-lifecycle.json`](s3-staging-lifecycle.json) and
[`s3-video-output-lifecycle.json`](s3-video-output-lifecycle.json).

## Versioning

Leave versioning disabled. The lifecycle rules above only expire current versions; enabling
versioning would require additional noncurrent-version expiration rules and is unnecessary
for short-lived staging and output objects.

## Bucket policy

Apply the restrictive bucket policies after replacing the placeholders. Each policy denies
`s3:*` to every principal whose `aws:PrincipalArn` is not the `motionly-inference` user or
the `<BREAK_GLASS_ROLE_NAME>` role, and denies any request where `aws:SecureTransport` is
`false` (non-TLS). `put-bucket-policy --bucket --policy` is a standard s3api command.

```bash
aws s3api put-bucket-policy \
  --bucket <STAGING_BUCKET> \
  --policy file://docs/aws/s3-staging-bucket-policy.json

aws s3api put-bucket-policy \
  --bucket <VIDEO_OUTPUT_BUCKET> \
  --policy file://docs/aws/s3-video-output-bucket-policy.json
```

See [`s3-staging-bucket-policy.json`](s3-staging-bucket-policy.json) and
[`s3-video-output-bucket-policy.json`](s3-video-output-bucket-policy.json).

### Lockout and recovery

A `Deny` on every principal except two is easy to lock yourself out of. Before applying:

- Make sure `<BREAK_GLASS_ROLE_NAME>` is a real role you can assume (for example, an
  administrator or operator role), not a placeholder, and that your current identity can
  assume it.
- Keep account-root access available as a last resort. The account root can always replace a
  bucket policy even when the policy denies it, which is the documented recovery path for an
  over-restrictive S3 bucket policy.
- Apply the policy to the staging bucket first, verify the inference user and the break-glass
  role still work, then apply it to the video bucket.

If you do lock the bucket, assume the break-glass role (or use the account root) and run
`aws s3api delete-bucket-policy --bucket <bucket>` or `put-bucket-policy` with a corrected
document.

### Caller-principal propagation (UNVERIFIED)

> UNVERIFIED - confirm in AWS console: S3 access for Bedrock features without a service role
> uses the calling identity's identity-based permissions (F10,
> <https://docs.aws.amazon.com/bedrock/latest/userguide/s3-bucket-access.html>). It is
> **not** confirmed that Bedrock Data Automation and async invoke carry the caller's
> `aws:PrincipalArn` all the way through to the bucket-policy `Deny` evaluation. If BDA or
> the video async invoke fails with `AccessDenied` on S3 after the IAM policy is correct,
> the bucket policy's `StringNotLike aws:PrincipalArn` deny is the likely cause.
>
> Test step: run a tiny BDA job (see [Data Automation project](data-automation-project.md))
> and a 5 s Luma Ray 2 invocation (see [Model access](model-access.md)) end to end. If S3
> access is denied, temporarily relax the principal deny to confirm it is the cause, then
> replace it with a least-privilege alternative (for example, allow the service principal or
> scope by `aws:SourceAccount`) and re-run.
