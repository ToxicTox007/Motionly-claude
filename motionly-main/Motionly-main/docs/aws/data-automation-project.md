# Bedrock Data Automation project

Transcription (`transcription.speech_to_text`) and diarization (`audio.diarization`) run
through a single Bedrock Data Automation (BDA) project in ap-south-1 that extracts the
audio transcript with speaker labels. This document creates the project, reads its ARN,
derives the cross-region data-automation profile ARN, shows the two registry fields to fill,
and runs a BDA smoke test with a 1-second WAV.

BDA is available in Asia Pacific (Mumbai) (F5,
<https://aws.amazon.com/about-aws/whats-new/2025/07/amazon-bedrock-data-automation-additional-aws-regions/>).

## 1. Create the project

The standard output configuration enables the audio TRANSCRIPT category with speaker
labeling. The shipped [`bda-audio-standard-output.json`](bda-audio-standard-output.json)
matches the API shape: `standardOutputConfiguration.audio.extraction.category` with `state`
`ENABLED`, `types` `[TRANSCRIPT]`, and `typeConfiguration.transcript.speakerLabeling.state`
`ENABLED` (F6,
<https://docs.aws.amazon.com/bedrock/latest/APIReference/API_data-automation_CreateDataAutomationProject.html>,
<https://docs.aws.amazon.com/bedrock/latest/APIReference/API_data-automation_AudioExtractionCategory.html>,
<https://docs.aws.amazon.com/bedrock/latest/APIReference/API_data-automation_SpeakerLabelingConfiguration.html>).

`projectType` is `ASYNC` (Motionly submits async jobs) and `projectStage` is `LIVE`
(F6). The CLI is confirmed against
<https://docs.aws.amazon.com/cli/latest/reference/bedrock-data-automation/create-data-automation-project.html>:

```bash
aws bedrock-data-automation create-data-automation-project \
  --region ap-south-1 \
  --project-name motionly-audio-transcript \
  --project-type ASYNC \
  --project-stage LIVE \
  --standard-output-configuration file://docs/aws/bda-audio-standard-output.json
```

> UNVERIFIED - confirm in AWS console: whether `generativeField` can be omitted or set to
> `DISABLED` without a `types` list. The shipped config enables only the TRANSCRIPT
> extraction category and does not set a generative field; if the API rejects that, add the
> minimal `generativeField` block the console shows and re-run.

## 2. Wait for the project and read its ARN

Creation returns a `projectArn` whose id matches
`data-automation-project/[a-zA-Z0-9-]{12,36}` (F6). Read the project back and confirm it is
ready before use:

```bash
aws bedrock-data-automation get-data-automation-project \
  --region ap-south-1 \
  --project-arn arn:aws:bedrock:ap-south-1:<ACCOUNT_ID>:data-automation-project/<BDA_PROJECT_ID>
```

Wait until the project's status reports it is created/completed and ready (the console shows
`Completed`). Capture the `<BDA_PROJECT_ID>` suffix of the returned `projectArn`.

## 3. Derive the data-automation profile ARN

BDA requires cross-region inference. The source ap-south-1 profile is

```
arn:aws:bedrock:ap-south-1:<ACCOUNT_ID>:data-automation-profile/apac.data-automation-v1
```

and it routes to ap-northeast-1, ap-northeast-2, ap-northeast-3, ap-south-1, ap-south-2,
ap-southeast-1, ap-southeast-2, and ap-southeast-4 (F5,
<https://docs.aws.amazon.com/bedrock/latest/userguide/bda-cris.html>). The IAM policy grants
`bedrock:InvokeDataAutomationAsync` on this profile ARN in all eight destination regions (see
[IAM inference policy](iam-inference-policy.md)).

## 4. Registry fields

Set these two fields on the `bedrock-data-automation` adapter in
`infra/ai-registry/registry.json` (owned by the registry stage):

```json
"profile_arn": "arn:aws:bedrock:ap-south-1:<ACCOUNT_ID>:data-automation-profile/apac.data-automation-v1",
"project_arn": "arn:aws:bedrock:ap-south-1:<ACCOUNT_ID>:data-automation-project/<BDA_PROJECT_ID>"
```

with `stage` `LIVE`, `s3_bucket` `<STAGING_BUCKET>`, `s3_region` `ap-south-1`, and `s3_prefix`
`bda`.

## 5. BDA smoke test (1-second WAV)

BDA accepts AMR, FLAC, M4A, MP3, Ogg, and WAV; duration 500 ms to 240 min; 8–48 kHz; up to 2
channels; up to 2048 MB (F5,
<https://docs.aws.amazon.com/bedrock/latest/userguide/bda-limits.html>). A 1-second mono 16
kHz WAV is well inside the limits.

Upload the input to the staging bucket under `bda/input/`, submit the job, and poll. The BDA
runtime CLI is confirmed against
<https://docs.aws.amazon.com/cli/latest/reference/bedrock-data-automation-runtime/invoke-data-automation-async.html>
and
<https://docs.aws.amazon.com/cli/latest/reference/bedrock-data-automation-runtime/get-data-automation-status.html>
(status values `Created`, `InProgress`, `Success`, `ServiceError`, `ClientError` — F11):

```bash
# Upload a 1-second WAV.
aws s3 cp sample-1s.wav s3://<STAGING_BUCKET>/bda/input/smoke.wav --profile motionly-inference --region ap-south-1

# Submit (bedrock-data-automation-runtime).
aws bedrock-data-automation-runtime invoke-data-automation-async \
  --profile motionly-inference \
  --region ap-south-1 \
  --input-configuration '{"s3Uri":"s3://<STAGING_BUCKET>/bda/input/smoke.wav"}' \
  --output-configuration '{"s3Uri":"s3://<STAGING_BUCKET>/bda/output/smoke"}' \
  --data-automation-profile-arn arn:aws:bedrock:ap-south-1:<ACCOUNT_ID>:data-automation-profile/apac.data-automation-v1 \
  --data-automation-configuration '{"dataAutomationProjectArn":"arn:aws:bedrock:ap-south-1:<ACCOUNT_ID>:data-automation-project/<BDA_PROJECT_ID>","stage":"LIVE"}'

# Poll.
aws bedrock-data-automation-runtime get-data-automation-status \
  --profile motionly-inference \
  --region ap-south-1 \
  --invocation-arn <INVOCATION_ARN_FROM_SUBMIT>
```

When the status is `Success`, the output under `bda/output/smoke/` contains the transcript
with speaker labels `spk_0`..`spk_29` (F6,
<https://docs.aws.amazon.com/bedrock/latest/userguide/audio-processing.html>). The staging
lifecycle rule expires `bda/` objects after 1 day, so no manual cleanup of the smoke test is
required (see [S3 buckets](s3-buckets.md)).

> UNVERIFIED - confirm in AWS console: the exact `--input-configuration`,
> `--output-configuration`, and `--data-automation-configuration` body shapes. Re-check the
> runtime CLI reference pages above before running; the option names are confirmed but the
> inner JSON keys should be verified against your CLI version.
