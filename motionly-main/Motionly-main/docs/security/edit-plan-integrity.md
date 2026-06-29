# Edit Plan Integrity

Persisted edit plans are validated before writes to `public.edit_plans` and before render
consumption. Generated plans must include the current schema version and prompt version, and
must pass timestamp, timeline, caption, B-roll, aspect ratio, layout preset, and render metadata
checks.

Invalid LLM output must not be persisted. The planner retries bounded attempts in this order:

1. `llm`
2. `llm_retry` with compact validation errors
3. `llm_minimal` with strict JSON-only instructions
4. `deterministic_fallback`

Validation errors are stored as compact structured strings with error code and path only. Logs and
metadata must not include raw prompts, full transcripts, signed URLs, secrets, or user media data.
Fallback plans are marked with `planner_source="deterministic_fallback"` and a `fallback_reason`.
