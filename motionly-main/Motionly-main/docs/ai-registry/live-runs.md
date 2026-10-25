# Live-run evidence log (Task 17)

**Revision 5 (2026-10-25): the live runs are DEFERRED.** They no longer gate
Task 19 item 2 or the Tier 1 gate. The archived vendor adapters are deleted now;
the tag `archive/pre-neutral-inference` (pushed to `origin`) remains the
reference implementation. `tests/live/test_live_template_routes.py` stays and
runs the **template route alone** against the real service whenever the operator
authorizes a run; there is no longer a second, archived side in the working tree
to compare against. The risk is accepted: a recorded fixture that no longer
matches the real API goes unnoticed until such a run. The rows below are
`DEFERRED`.

Originally (revision 4) one live provider run was **mandatory** for each of the
three ported capabilities before Task 19 deleted its vendor adapter. Fakes can
drift from the real APIs, and after deletion nothing real is left in the tree to
compare against. This file is the evidence log an authorized run writes to.

## Comparing against the archived adapters

The archived adapters are not in the working tree anymore. To compare the
template route against the original vendor adapter on the same sample, run the
adapter from a checkout of the archive tag:

```bash
# In a throwaway worktree at the archived snapshot (never on main):
git worktree add /tmp/pre-neutral archive/pre-neutral-inference
cd /tmp/pre-neutral/motionly-main/Motionly-main/apps/api
# Build the archived adapter (app/services/google_vision.py,
# pyannoteai_diarization.py, lalal_stems.py) against the real service on the same
# operator sample, then diff its typed output and request sequence against this
# repository's live-run result for the same capability.
```

The plan tolerances for such a comparison (objects: same label set, box corners
within 0.02; diarization: same speaker count, segment boundaries within 0.5 s;
stems: `vocals` and `no_vocals` present and non-empty, each within 0.1 s of the
source duration) are documented below.

## Purpose

For each capability, one run exercises the **template route** against the real
service on an operator-supplied sample and records a redacted response shape
(keys and types only, never values), so the fake fixtures can be refreshed if the
real shape drifts.

## How to run

The run is manual, operator-approved and paid. It never runs in CI
(invariant 9); the test guard stands down only when `MOTIONLY_LIVE_INFERENCE=1`.

```bash
cd apps/api
MOTIONLY_LIVE_INFERENCE=1 \
MOTIONLY_LIVE_REGISTRY=/path/to/operator-registry.json \
AI_SECRETS_BUNDLE_FILE=/path/to/secret-bundle.json \
MOTIONLY_LIVE_SAMPLES_DIR=/path/to/samples \
MOTIONLY_LIVE_CAPABILITIES=objects,diarization,stems \
MOTIONLY_LIVE_RESULTS_DIR=/path/to/results \
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/live/test_live_template_routes.py
```

Environment inputs:

| Variable                     | Meaning                                                                             |
| ---------------------------- | ----------------------------------------------------------------------------------- |
| `MOTIONLY_LIVE_INFERENCE=1`  | authorizes the run (only case the network guard stands down)                        |
| `MOTIONLY_LIVE_REGISTRY`     | operator registry with the example routes (real base URLs/secrets)                  |
| `AI_SECRETS_BUNDLE_FILE`     | the secret bundle the registry references                                           |
| `MOTIONLY_LIVE_SAMPLES_DIR`  | samples `objects.jpg`, `two-speakers.wav`, `voice-over-music.wav` (never committed) |
| `MOTIONLY_LIVE_CAPABILITIES` | optional subset (`objects,diarization,stems`)                                       |
| `MOTIONLY_LIVE_RESULTS_DIR`  | optional; where the machine-readable result is written                              |

The samples are an image with known objects, about 30 s of two-speaker speech,
and about 30 s of voice over music. They are never committed; neither are
secrets, signed URLs or media. The machine-readable result records the date,
route id, revision, the sample SHA-256, the outcome, the provider-reported cost
when available, and a redacted copy of each live response shape (keys and types
only) for fixture comparison.

The CI workflow that drives an operator-triggered run is added in Task 26; until
then the run is invoked by hand as above.

### Tolerances (plan Task 17 pass criteria)

| Capability  | Tolerance                                                                                |
| ----------- | ---------------------------------------------------------------------------------------- |
| objects     | same label set; box corners within 0.02                                                  |
| diarization | same speaker count; segment boundaries within 0.5 s                                      |
| stems       | `vocals` and `no_vocals` present and non-empty; each within 0.1 s of the source duration |

The template must send the request sequence recorded in the capability's live
scenario (`live.json`): the same methods, path shapes, header names and body
keys. Cleanup at the provider is confirmed where the route's mapping has a
`cleanup` stage, and no secret value may appear in the captured logs. The
tolerance table above is used only when comparing with an archived adapter from a
checkout of the tag (see "Comparing against the archived adapters").

## Hard-cap rule

Each live run is bounded by a `budget.hard_cap` of a few requests on the example
route, so an authorized run cannot spend more than a handful of calls. Keep the
cap in the registry used for the run; do not raise it to re-run.

## Evidence

One row per capability. `outcome` is `DEFERRED` (revision 5): the run no longer
gates Task 19 item 2 or the Tier 1 gate and happens only when an operator
authorizes it. On an authorized run, record the date, operator, sample SHA-256,
outcome (`pass`/`fail`), cost and `fixture diff` (`none` or `updated` — the
redacted live shape matched the fakes, or the fakes were refreshed and the
conformance suite re-run).

| date | operator | route id                   | revision                    | sample SHA-256 | outcome  | cost | fixture diff |
| ---- | -------- | -------------------------- | --------------------------- | -------------- | -------- | ---- | ------------ |
| —    | —        | `vision-objects-remote`    | `builtin-stable-2026-10-22` | —              | DEFERRED | —    | —            |
| —    | —        | `audio-diarization-remote` | `precision-v3`              | —              | DEFERRED | —    | —            |
| —    | —        | `audio-stems-remote`       | `andromeda-v6`              | —              | DEFERRED | —    | —            |

Never record secrets, signed URLs or media in this file.
