# AI registry examples and template scenarios (Task 17)

This directory holds the operator-shaped example registry documents and the
template _scenarios_ that the Task 17 conformance, differential and live suites
run. It is the shared surface the three capability agents (object detection,
diarization, stem separation) build on.

## Layout

```
docs/ai-registry/examples/
  <capability>.json                 example registry document (one shape)
  <capability>-alt.json             a second, differently-shaped provider
  scenarios/
    <capability-id>/                literal id, e.g. vision.object_detection
      <name>.json                   a scenario
      media/                        images/audio a scenario references
```

`<capability-id>` is the fine-grained id exactly as written in a route's
`capability` field: `vision.object_detection`, `audio.diarization`,
`audio.stem_separation`.

Vendor and model names may appear only in an example document's `model_id`,
`base_url`, `provider`, `data_processor` and `mapping.artifacts.allowed_hosts`
fields — these are operator configuration, and the Task 2 scanner allows them
there. They may not appear in any `id`, `adapter_id` or `model_revision` field;
the conformance suite and the scanner both check this.

## Scenario schema

A scenario is one JSON object. The harness
(`apps/api/tests/support/template_scenarios.py`) documents every field in its
module docstring; the summary:

| Field                      | Required            | Meaning                                                               |
| -------------------------- | ------------------- | --------------------------------------------------------------------- |
| `registry`                 | yes                 | file name of the example registry document to build from              |
| `route_id`                 | yes                 | the route id to exercise                                              |
| `capability`               | no                  | fine-grained id; defaults to the parent directory name                |
| `request`                  | yes                 | typed request spec; `type` selects the builder                        |
| `exchanges`                | yes (template)      | ordered fake-provider match/response list                             |
| `expect`                   | no                  | any of `output`, `usage`, `error`, `requests`, `cleanup`, `operation` |
| `differential`             | no (default `true`) | pair with the archived adapter                                        |
| `differential_skip_reason` | no                  | why `differential` is `false`                                         |

A **media ref** inside `request` is `{"file": "<name>"}` (a file under the
scenario's `media/` directory) or `{"b64": "<base64>"}` (tiny inline bytes).

An **exchange** is `{"match": {...}, "response": {...}, "repeat": <int|"always">}`.
`match` has `method`, optional `host` (the logical Host the server sees; the base
URL host when omitted) and one of `path` or `path_prefix`. `response` has
`status`, one of `json` / `body_b64` / `body_file`, and optional `headers`.
`repeat` defaults to `1`; `"always"` serves a terminal poll status indefinitely.

## A complete minimal example

A synchronous `generic_http` object-detection scenario against a provider that
returns one result per input image:

```json
{
  "registry": "object-detection.json",
  "route_id": "vision-objects-remote",
  "capability": "vision.object_detection",
  "request": {
    "type": "object_detection",
    "max_results": 5,
    "images": [{ "file": "one-car.png" }]
  },
  "exchanges": [
    {
      "match": { "method": "POST", "path": "/v1/images:annotate" },
      "response": {
        "status": 200,
        "json": {
          "responses": [
            {
              "localizedObjectAnnotations": [
                {
                  "name": "Car",
                  "score": 0.97,
                  "boundingPoly": {
                    "normalizedVertices": [
                      { "x": 0.1, "y": 0.2 },
                      { "x": 0.5, "y": 0.2 },
                      { "x": 0.5, "y": 0.7 },
                      { "x": 0.1, "y": 0.7 }
                    ]
                  }
                }
              ]
            }
          ]
        }
      }
    }
  ],
  "expect": {
    "output": {
      "detections": [
        [{ "label": "car", "score": 0.97, "box": [0.1, 0.2, 0.4, 0.5] }]
      ]
    },
    "usage": { "input_units": 1 },
    "requests": [
      {
        "method": "POST",
        "host": "vision.example.invalid",
        "path": "/v1/images:annotate",
        "header_names": ["accept", "authorization"],
        "body_keys": ["requests"]
      }
    ]
  }
}
```

The harness builds the route from `object-detection.json` with
`build_inference_runtime`, serves the single exchange through an `httpx`
MockTransport (the pinned-IP Host header is handled exactly as in
`apps/api/tests/test_async_job_adapter.py`), invokes the typed request through
the real gateway, and compares the resulting `Observation` against `expect`.

## Harness smoke example

`apps/api/tests/fixtures/template_scenarios/` holds a synthetic `generic_http`
scenario (`smoke.json` + `smoke-objects.registry.json`) that proves the runner
works before any capability agent lands. It is not a vendor example and is not
part of `docs/ai-registry/examples/`.
