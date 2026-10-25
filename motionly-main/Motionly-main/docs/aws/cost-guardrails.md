# Cost guardrails

Motionly's Bedrock deployment has two independent spend guards, and you should keep both:

1. **AWS Budgets** — an account-wide monthly cost budget that emails you as spend rises. It is a
   lagging, alert-only backstop: it never stops a request.
2. **Registry per-route hard caps** — monthly **and** daily request/unit caps enforced in the
   app before any provider is contacted. This is the real-time guard, and it is the one that
   actually prevents a runaway bill during the first test.

The emergency stop for both is to **deactivate the inference user's access key** (see
[IAM inference user and policy](iam-inference-policy.md) and the end of this page).

Every value in the JSON templates on this page is a placeholder. Replace `<ALERT_EMAIL>`,
`<MONTHLY_BUDGET_USD>` and `<ACCOUNT_ID>` with your own; never commit a real email or account id.

## AWS Budgets (lagging backstop)

Create one account-wide monthly **cost** budget. Because it has no `CostFilters`, it covers
every service in the account — including Luma Ray 2, which is billed through AWS Marketplace and
shown in Cost Explorer under the model provider rather than under Amazon Bedrock (F8). A budget
scoped to the Bedrock service alone would miss Luma spend, so keep it account-wide.

The two templates in this directory are the exact `--budget` and
`--notifications-with-subscribers` shapes that
[`aws budgets create-budget`](https://docs.aws.amazon.com/cli/latest/reference/budgets/create-budget.html)
accepts:

- [`budget.json`](budget.json) — `BudgetType: COST`, `TimeUnit: MONTHLY`,
  `BudgetLimit.Amount` `<MONTHLY_BUDGET_USD>` (suggested starting value: `10`), `Unit: USD`.
- [`budget-notifications.json`](budget-notifications.json) — four notifications (the API allows
  at most five per budget, F12): `ACTUAL` at 50%, 80% and 100%, plus `FORECASTED` at 100%. Each
  notification uses `ComparisonOperator: GREATER_THAN`, `ThresholdType: PERCENTAGE`, and one
  `EMAIL` subscriber `<ALERT_EMAIL>`.

```bash
# Fill <MONTHLY_BUDGET_USD> in budget.json and <ALERT_EMAIL> in
# budget-notifications.json first. AWS Budgets is a global service; the console
# and API use the us-east-1 endpoint. [UNVERIFIED - confirm in AWS console]
aws budgets create-budget \
  --account-id <ACCOUNT_ID> \
  --budget file://budget.json \
  --notifications-with-subscribers file://budget-notifications.json \
  --region us-east-1
```

The first email to `<ALERT_EMAIL>` is an SNS-style subscription confirmation for each
notification; confirm it or the alerts never arrive.

**Budgets lags spend.** Budget data refreshes up to three times a day, typically 8–12 hours
apart, so an alert can arrive hours after the money is spent (F12,
<https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html>).
Budgets only alerts; it does not cap or stop spend. Treat it as a safety net behind the registry
caps, not as the primary control.

## Registry per-route hard caps (real-time guard)

Every Bedrock route in [`infra/ai-registry/registry.json`](../../infra/ai-registry/registry.json)
declares `budget.hard_cap` with **both** a monthly and a daily cap. These are enforced in the
model gateway before the provider is contacted
(`apps/api/app/services/model_gateway.py:1670-1755`, `:1972-1990`), so an exhausted cap prevents
the paid call from ever being sent.

### Caps are per route

A cap is keyed by `route_id`, not by capability, workspace or provider
(`apps/api/app/services/model_gateway.py:2482-2516`). Each shipped route serves one capability,
so the per-route cap reads per-capability. The counters live in Redis
(`apps/api/app/services/inference_runtime.py:1083-1100`), so a cap is **fleet-wide** across every
API and worker container pointed at the same Redis, not per container.

### The shipped first-test caps

| Capability                     | Route id                   | Cap unit        | Daily cap | Monthly cap |
| ------------------------------ | -------------------------- | --------------- | --------- | ----------- |
| `planning.chat`                | `bedrock-planning-chat`    | requests        | 50        | 500         |
| `transcription.speech_to_text` | `bedrock-transcription`    | units (seconds) | 1800      | 18000       |
| `audio.diarization`            | `bedrock-diarization`      | units (seconds) | 1800      | 18000       |
| `vision.object_detection`      | `bedrock-object-detection` | requests        | 200       | 2000        |
| `vision.caption`               | `bedrock-caption`          | requests        | 200       | 2000        |
| `embeddings.text`              | `bedrock-embeddings-text`  | requests        | 500       | 5000        |
| `video.generation.submit`      | `bedrock-video-generation` | requests        | 3         | 20          |

The local CPU routes (`local-stem-separation`, `local-matte-segmentation`,
`local-sound-classification`) carry no cap and are never blocked by a hard cap — they run on your
own CPU for free.

These numbers are deliberately small for a first paid test. Raise them deliberately once you have
confirmed real pricing and filled the `FILL FROM AWS PRICING PAGE` markers.

### What happens when a cap is hit, per capability

When a route reserves its last unit, the gateway skips that route with the error code
`route_hard_cap_exhausted` and falls back to the next priority route for the capability. In the
shipped registry each capability has a single Bedrock route, so there is no fallback and the call
fails with `route_hard_cap_exhausted` (surfaced to callers, to `/health/dependencies`, and to the
admin route test). Verified behaviour per capability (from the daily-caps stage, cited to
`apps/api/app/services/model_gateway.py`):

- **planning** (`bedrock-planning-chat`, Converse): the planning route is skipped; with no
  fallback planner the plan request fails with `route_hard_cap_exhausted`.
- **transcription** (`bedrock-transcription`, BDA): the transcription route is skipped; the unit
  cap counts billable **seconds** of audio (`estimate_input_units`,
  `model_gateway.py:2343+`).
- **diarization** (`bedrock-diarization`, BDA): same as transcription; units counted as
  diarization billable seconds.
- **object detection** (`bedrock-object-detection`, Converse): the detection route is skipped;
  the request cap counts calls (one input unit per image for a unit cap).
- **captions** (`bedrock-caption`, Converse): the caption route is skipped.
- **embeddings** (`bedrock-embeddings-text`, InvokeModel Titan): the embeddings route is skipped.
- **video generation** (`bedrock-video-generation`, async Luma Ray): the **submit** call consumes
  the cap and is skipped when the cap is full. The **poll** call
  (`VideoGenerationPollRequest`, `model_gateway.py:266`, `:1691-1693`) never consumes the cap, so
  polling an already-submitted job is unaffected by an exhausted cap.

A poll/status request never consumes a cap. A reservation is released on a non-billable failure
and on a dollar-budget or quota rejection (`model_gateway.py:1996`, `:2026`, `:2083-2086`); a
**billable** provider failure settles the cap because the provider may already have run
(`model_gateway.py:2081-2082`); a success commits the exact reserved count
(`model_gateway.py:1757-1763`).

### When caps reset

Both scopes use UTC:

- **Daily cap** — key `route-hard-cap-daily:{unit}:{route_id}:YYYY-MM-DD`
  (`model_gateway.py:2497-2516`). Resets at UTC midnight (the next UTC calendar day gets a fresh
  counter).
- **Monthly cap** — key `route-hard-cap:{unit}:{route_id}:YYYY-MM`
  (`model_gateway.py:2482-2495`). Resets at the start of the next UTC month.

The daily and monthly scopes are disjoint keys on the same Redis ledger, and `{unit}`
(`requests` vs `units`) keeps the request-count and billing-unit caps separate, so a route can
have both an exhausted daily request cap and headroom on its monthly unit cap at the same time.

### The unknown-pricing rule is unchanged

`has_positive_cap` still counts only the monthly caps (`inference_registry.py:405-409`), so a
route priced `unknown` still requires a positive **monthly** hard cap; a daily cap alone does not
satisfy it. Every shipped Bedrock route is priced `priced` (with the pricing markers to fill), so
this rule does not block them — it is only a floor for unknown-priced routes you might add later.

## App-side spend prediction

Before you rely on real Cost Explorer numbers (which lag a day), the app's own estimate is the
fastest signal:

- `GET /health/dependencies` (authenticated) — the `inference` entry lists each route's circuit
  state, validation warnings, dropped routes, and surfaces `route_hard_cap_exhausted` when a cap
  is spent. This is where you see a cap being hit in real time.
- Optional deployment-wide monthly USD caps per feature are set with
  `MODEL_GATEWAY_FEATURE_BUDGETS_USD` (JSON; empty = none;
  `infra/docker-compose.remote-ai.yml:74`). These are dollar budgets per feature, independent of
  the per-route request/unit hard caps above.

## Emergency stop

The hard caps are the real-time guard, but to stop **all** Bedrock spend immediately:

1. **Deactivate the inference user's access key.** This is the single fastest kill switch; every
   container reads the same two secret files, and without valid credentials the adapter fails
   closed with `provider_auth_failed` and sends no HTTP request. See
   [IAM inference user and policy](iam-inference-policy.md) for the deactivate/rotate commands.
2. Optionally disable individual routes (`"enabled": false`) or restore the previous registry and
   redeploy — slower, but lets you keep some capabilities running.

Deactivating the key does not stop Luma Marketplace charges already incurred for an in-flight
video job; it only prevents new invocations. The full rollback sequence is in
[First paid test](first-paid-test.md).
