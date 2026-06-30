# Publishing Reliability

Phase 7 adds fail-closed publishing behavior for OAuth expiry, provider throttling, and uncertain remote publish state.

## Connection states

- `active`: the connection can be used for publish attempts.
- `reconnect_required`: the provider rejected refresh or required scopes changed. New publish attempts stop before upload.

The API surfaces `last_refresh_at` and `last_refresh_failure_reason` so reconnect issues are visible without exposing tokens.

## Refresh behavior

- Access tokens refresh on use when expiry is within `PUBLISH_TOKEN_REFRESH_BUFFER_SECONDS`.
- Scheduled refresh can be driven through `refresh_connections_due_for_publish(...)`.
- Refresh failures never log access tokens, refresh tokens, or client secrets.

## Publish statuses

- `queued`
- `refreshing_token`
- `uploading`
- `processing`
- `published`
- `retry_scheduled`
- `reconnect_required`
- `duplicate_risk_needs_review`
- `failed`

## Retry policy

- Provider `429` responses map to `provider_rate_limited`.
- Retry delay prefers provider `Retry-After`, then bounded exponential backoff using:
  - `PUBLISH_RETRY_BACKOFF_SECONDS`
  - `PUBLISH_RETRY_BACKOFF_CAP_SECONDS`
  - `PUBLISH_RETRY_MAX_ATTEMPTS`
- Retryable network and provider availability failures reuse the same bounded retry policy.
- Non-retryable auth, scope, permission, and format failures fail closed.

## Duplicate publish protection

- New publish jobs are rejected when another active publish exists for the same `workspace_id + export_id + connection_id`.
- `publish_jobs_inflight_dedupe_idx` enforces the same rule in Postgres.
- If a provider has already accepted work but final state is uncertain, the job moves to `duplicate_risk_needs_review` instead of retrying blindly.

## Safe logging

Structured publish logs may include:

- provider
- workspace, project, and publish job ids
- retry delay
- error code/category

They must not include OAuth tokens, client secrets, signed URLs, raw provider credential payloads, or user media URLs.
