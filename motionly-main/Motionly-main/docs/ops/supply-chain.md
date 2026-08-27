# Motionly supply-chain pins

This file is the reviewable source of truth for external image and model revisions. Application images may carry commit tags for traceability, but Azure VM deployments must use the immutable GHCR digest recorded in the signed release manifest—never `latest` or a mutable tag.

## Public container images

```text
python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28
node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0
alpine:3.21.3@sha256:a8560b36e8b8210634f77d9f7f9efd7ffa463e380b75e2e74aff4511df3ef88c
redis:7.4-alpine@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99
supabase/postgres:17.4.1.069@sha256:6900ef3bde159491c8774249dfd213926416f716ef0c897897ae4f88e6819c72
supabase/gotrue:v2.170.0@sha256:0cfa54e096dcae4ed30162811fa8794f7c443708c41973329e07f7d44c1cc3e7
postgrest/postgrest:v12.2.8@sha256:e5978740a590628f2114730bb942f35342ea70b8b7a86697a3df283c0caea109
supabase/storage-api:v1.14.5@sha256:ee0826bde4966c4cb70b79605ea5197d708dfa6ceeee7f427c4cad8b539ffda3
kong:2.8.1@sha256:1b53405d8680a09d6f44494b7990bf7da2ea43f84a258c59717d4539abf09f6d
caddy:2.8-alpine@sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17
```

## Model revisions

```text
openai/clip-vit-base-patch32@3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268
Salesforce/blip2-opt-2.7b@59a1ef6c1e5117b3f65523d1c6066825bcf315e3
superb/wav2vec2-base-superb-er@441a7599c3b22107314dcbd9166621c5c83f2cc5
pyannote/speaker-diarization-3.1@84fd25912480287da0247647c3d2b4853cb3ee5d
ultralytics-assets-v8.3.0/yolov8n.pt
demucs==4.1.0 / htdemucs
https://tfhub.dev/google/yamnet/1
```

The `Supply chain` workflow generates SPDX SBOM artifacts, runs CodeQL, Trivy, and license checks, and signs commit-addressed GHCR images with Cosign on push events.
