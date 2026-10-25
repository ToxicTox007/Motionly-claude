# Model access and smoke tests

This document lists every Amazon Bedrock model Motionly's registry
(`infra/ai-registry/registry.json`) routes to, how to enable access, and a tiny smoke test
for each. Serverless Amazon models are enabled by default and the subscription starts on the
first invocation; Luma Ray 2 is an AWS Marketplace model and needs a first-time enablement by
an administrator (F9,
<https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html>).

All smoke tests run as the inference user with `--profile motionly-inference`. Use tiny
inputs. Replace `<ACCOUNT_ID>`, `<STAGING_BUCKET>`, `<VIDEO_OUTPUT_BUCKET>`, and
`<BDA_PROJECT_ID>` with your values.

## Model table

| Capability                                          | Model / profile id                                                | Region            | API                      | In-region / Geo                       | Access requirement                                                    | Cost note                                                                                              |
| --------------------------------------------------- | ----------------------------------------------------------------- | ----------------- | ------------------------ | ------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `planning.chat`                                     | `apac.amazon.nova-pro-v1:0` (profile)                             | ap-south-1 → APAC | Converse                 | In-Region NO, Geo YES (F3)            | Serverless, on by default                                             | Token priced; see pricing page                                                                         |
| `vision.object_detection`, `vision.caption`         | `apac.amazon.nova-lite-v1:0` (profile)                            | ap-south-1 → APAC | Converse                 | In-Region NO, Geo YES (F3)            | Serverless, on by default                                             | Token priced                                                                                           |
| `embeddings.text`                                   | `amazon.titan-embed-text-v2:0`                                    | ap-south-1        | InvokeModel              | In-Region YES, Geo NO (F4)            | Serverless, on by default                                             | Input-token priced                                                                                     |
| `transcription.speech_to_text`, `audio.diarization` | BDA profile `apac.data-automation-v1`, project `<BDA_PROJECT_ID>` | ap-south-1 → APAC | Bedrock Data Automation  | Requires cross-region inference (F5)  | Enable BDA; create project                                            | Per-second priced                                                                                      |
| `video.generation.submit`                           | `luma.ray-v2:0`                                                   | us-west-2         | StartAsyncInvoke (async) | In-Region us-west-2 only, Geo NO (F8) | AWS Marketplace; admin enables (F9); **accepted 2026-10-25**          | Billed via Marketplace, shown under the model provider in Cost Explorer, not under Amazon Bedrock (F8) |
| `audio.sound_classification` (sound)                | `yamnet-tflite`                                                   | local CPU         | n/a                      | n/a                                   | None (no AWS)                                                         | Free, local                                                                                            |
| `matte.segmentation` (matte)                        | `mediapipe-selfie-segmentation`                                   | local CPU         | n/a                      | n/a                                   | None (no AWS)                                                         | Free, local                                                                                            |
| `audio.stem_separation` (stems)                     | `htdemucs` (Demucs)                                               | local CPU         | n/a                      | n/a                                   | None (no AWS)                                                         | Free, local                                                                                            |
| Nova Reel 1.1 (NOT used)                            | `amazon.nova-reel-v1:1`                                           | us-east-1 only    | async                    | EOL                                   | **End of life 2026-09-30, fails with ResourceNotFoundException** (F7) | n/a                                                                                                    |

The APAC destination regions for the Nova profiles (source ap-south-1) are ap-northeast-1,
ap-northeast-2, ap-northeast-3, ap-south-1, ap-southeast-1, ap-southeast-2 (F3). BDA's APAC
profile adds ap-south-2, ap-southeast-4 (F5). The local CPU routes make no AWS calls and need
no model access; they run in the Demucs worker and the CPU vision worker.

## Enable model access

- **Amazon Nova Pro, Nova Lite, Titan Embeddings V2:** serverless Amazon models are enabled
  by default; the subscription starts on first invocation and Amazon models are not sold via
  Marketplace (F9). The smoke tests below trigger the subscription.
- **Bedrock Data Automation:** BDA is available in Asia Pacific (Mumbai) (F5,
  <https://aws.amazon.com/about-aws/whats-new/2025/07/amazon-bedrock-data-automation-additional-aws-regions/>).
  Create the project first (see [Data Automation project](data-automation-project.md)).
- **Luma Ray 2:** this is an AWS Marketplace model (product `prod-bi33bxqeqavl6`). First-time
  enablement needs `aws-marketplace:Subscribe`, `aws-marketplace:Unsubscribe`, and
  `aws-marketplace:ViewSubscriptions` (F9). Have an **administrator** perform the first Luma
  Ray 2 invocation (or enable model access in the console) so the `motionly-inference` user
  never needs Marketplace permissions. After enablement, the inference user's
  `bedrock:InvokeModel` on the model ARN is enough.

## Smoke tests

### Nova Pro (planning.chat) — Converse, ap-south-1

```bash
aws bedrock-runtime converse \
  --profile motionly-inference \
  --region ap-south-1 \
  --model-id apac.amazon.nova-pro-v1:0 \
  --messages '[{"role":"user","content":[{"text":"Reply with the single word: ok"}]}]' \
  --inference-config '{"maxTokens":8}'
```

`aws bedrock-runtime converse --model-id --messages --inference-config` is confirmed against
the AWS CLI reference (<https://docs.aws.amazon.com/cli/latest/reference/bedrock-runtime/converse.html>).

### Nova Lite (vision.object_detection, vision.caption) — Converse, ap-south-1

```bash
aws bedrock-runtime converse \
  --profile motionly-inference \
  --region ap-south-1 \
  --model-id apac.amazon.nova-lite-v1:0 \
  --messages '[{"role":"user","content":[{"text":"Reply with the single word: ok"}]}]' \
  --inference-config '{"maxTokens":8}'
```

A real vision call includes an image content block; this text-only call only proves access
and routing.

### Titan Embeddings V2 (embeddings.text) — InvokeModel, ap-south-1

`invoke-model` requires a positional outfile and `--cli-binary-format raw-in-base64-out` for
a raw JSON body (F11,
<https://docs.aws.amazon.com/cli/latest/reference/bedrock-runtime/invoke-model.html>).

```bash
aws bedrock-runtime invoke-model \
  --profile motionly-inference \
  --region ap-south-1 \
  --model-id amazon.titan-embed-text-v2:0 \
  --cli-binary-format raw-in-base64-out \
  --body '{"inputText":"ok"}' \
  /dev/stdout
```

### Bedrock Data Automation (transcription.speech_to_text, audio.diarization) — ap-south-1

The BDA smoke test uses a 1-second WAV and the APAC data-automation profile; see
[Data Automation project](data-automation-project.md) for the full `invoke-data-automation-async`
and `get-data-automation-status` commands.

### Luma Ray 2 (video.generation.submit) — StartAsyncInvoke, us-west-2

Start a 5 s video and poll the invocation. The output bucket must be in us-west-2 (F8). Have
an administrator run this once first if Marketplace access is not yet enabled (F9).

```bash
# Start (StartAsyncInvoke is authorized by bedrock:InvokeModel; there is no
# bedrock:StartAsyncInvoke action - F1).
aws bedrock-runtime start-async-invoke \
  --profile motionly-inference \
  --region us-west-2 \
  --model-id luma.ray-v2:0 \
  --model-input '{"prompt":"a calm ocean at sunrise","duration":"5s","resolution":"720p","aspect_ratio":"16:9"}' \
  --output-data-config '{"s3OutputDataConfig":{"s3Uri":"s3://<VIDEO_OUTPUT_BUCKET>/video/"}}'

# Poll (get-async-invoke takes --invocation-arn - F11).
aws bedrock-runtime get-async-invoke \
  --profile motionly-inference \
  --region us-west-2 \
  --invocation-arn <INVOCATION_ARN_FROM_START>
```

> UNVERIFIED - confirm in AWS console: the exact `start-async-invoke` option names
> (`--model-input`, `--output-data-config`, and the Luma `model-input` body shape). Luma Ray
> 2 supports durations 5s and 9s and aspect ratios including 9:16 and 16:9 (F8,
> <https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-luma.html>). Re-check
> the body and option names against the AWS CLI reference before running.

### Local CPU routes (sound, matte, stems)

No AWS call and no model access. They run inside the Demucs worker (stems) and the CPU vision
worker (matte, sound). Verify them through the application's capability health rather than a
Bedrock smoke test.

## Nova Reel is not used

Nova Reel 1.1 was available only in US East (N. Virginia) and 1.0 in us-east-1, eu-west-1,
ap-northeast-1 (F7, <https://docs.aws.amazon.com/nova/latest/userguide/video-generation.html>).
Nova Reel reached end of life on 2026-09-30 and now returns `ResourceNotFoundException` (F7).
Motionly uses Luma Ray 2 in us-west-2 instead. The row is listed only so operators recognize
and avoid the retired model.
