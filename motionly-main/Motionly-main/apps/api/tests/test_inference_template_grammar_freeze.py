"""Grammar-freeze test (plan Section 2.10, rule 2).

This file holds a LITERAL copy of the Section 2.10 "Grammar freeze" inventory —
value forms, functions, ``generic_http`` keys and their enum values, and the
route options — and asserts that the constants ``inference_templates`` exports
equal it exactly.

If code adds a grammar element, this test fails until BOTH this literal copy and
Section 2.10 of the plan are changed together. That is the point: a code-only
addition to the mapping language cannot pass CI. Any change here requires a plan
revision whose changelog entry justifies the new element (Section 2.10, rule 3).

The ``async_job`` rows are intentionally left as clearly-marked placeholders that
Task 16B fills in when it adds the ``async_job`` slice to the validator.
"""

from __future__ import annotations

from app.services import inference_templates as templates

# ---------------------------------------------------------------------------
# LITERAL copy of the Section 2.10 inventory. Do not import from the module:
# this is the independent reference the module is pinned against.
# ---------------------------------------------------------------------------

# Value forms (Section 2.10 "Value forms", count 6).
SECTION_2_10_VALUE_FORMS = {
    "json_literal",
    "ref",
    "fn",
    "map",
    "path_placeholder",
    "vars_block",
}

# $ref roots: input, params, vars, response, item.
SECTION_2_10_REF_ROOTS = {"input", "params", "vars", "response", "item"}

# $ref path segments: key, [n], [*], {vars.<name>}.
SECTION_2_10_REF_SEGMENTS = {"key", "[n]", "[*]", "{vars.<name>}"}

# Functions (the complete list, count 9).
SECTION_2_10_FUNCTIONS = {
    "base64",
    "data_url",
    "uuid",
    "concat",
    "to_int",
    "to_float",
    "to_ms",
    "clamp01",
    "default",
}

# generic_http keys (count 9): request (method/path/body); items; aligned_to_input;
# fields; box_format (vertices_norm/xywh_norm); item_error; item_error_codes
# (rejected/unavailable); error_map (rejected/unavailable/auth/quota/malformed);
# usage (count_items/count_ok_items).
SECTION_2_10_GENERIC_HTTP_KEYS = {
    "request",
    "items",
    "aligned_to_input",
    "fields",
    "box_format",
    "item_error",
    "item_error_codes",
    "error_map",
    "usage",
}
SECTION_2_10_GENERIC_HTTP_REQUEST_KEYS = {"method", "path", "body"}
SECTION_2_10_GENERIC_HTTP_FIELD_KEYS = {"detections", "label", "score", "box"}
SECTION_2_10_BOX_FORMATS = {"vertices_norm", "xywh_norm"}
SECTION_2_10_ITEM_ERROR_CLASSES = {"rejected", "unavailable"}
SECTION_2_10_ERROR_MAP_CLASSES = {"rejected", "unavailable", "auth", "quota", "malformed"}
SECTION_2_10_USAGE_MODES = {"count_items", "count_ok_items"}

# Route options (count 3): input_transform.audio, upload_filename, label_aliases.
SECTION_2_10_ROUTE_OPTIONS = {
    "input_transform.audio",
    "upload_filename",
    "label_aliases",
}

# ---------------------------------------------------------------------------
# async_job rows (Section 2.10 "async_job keys", count 6) — LITERAL copy.
#
# The frozen async_job grammar has six groups (Section 2.10 freeze table
# "Elements", count 6): steps, commit, poll, result, artifacts, cleanup. The
# ``vars`` block is a value form (counted among the value forms), so the
# top-level async_job mapping accepts ``vars`` plus the five container groups.
# ---------------------------------------------------------------------------

# Top-level async_job mapping keys: the ``vars`` initializer plus the container
# groups steps/poll/result/artifacts/cleanup. ``commit`` is a per-step flag.
SECTION_2_10_ASYNC_JOB_KEYS = {
    "vars",
    "steps",
    "poll",
    "result",
    "artifacts",
    "cleanup",
}

# The six frozen async_job grammar groups.
SECTION_2_10_ASYNC_JOB_GROUPS = {
    "steps",
    "commit",
    "poll",
    "result",
    "artifacts",
    "cleanup",
}

# Step sub-keys: method, path, url (auth:none only), body (JSON or input.file),
# capture, require (https), auth (adapter/none), commit.
SECTION_2_10_ASYNC_JOB_STEP_KEYS = {
    "method",
    "path",
    "url",
    "body",
    "capture",
    "require",
    "auth",
    "commit",
}
SECTION_2_10_ASYNC_JOB_AUTH_MODES = {"adapter", "none"}
SECTION_2_10_ASYNC_JOB_REQUIRE_PREDICATES = {"https"}

# poll sub-keys: method, path, body, status, status_map, tolerate_retryable,
# interval_s, backoff, max_wait_s.
SECTION_2_10_ASYNC_JOB_POLL_KEYS = {
    "method",
    "path",
    "body",
    "status",
    "status_map",
    "tolerate_retryable",
    "interval_s",
    "backoff",
    "max_wait_s",
}
SECTION_2_10_ASYNC_JOB_BACKOFF_KEYS = {"factor", "max_interval_s"}
SECTION_2_10_ASYNC_JOB_STATUS_FIELDS = {"state", "billable", "fallback"}
SECTION_2_10_ASYNC_JOB_STATUS_STATES = {
    "running",
    "succeeded",
    "failed",
    "retryable_failed",
}

# result sub-keys: items, fields.
SECTION_2_10_ASYNC_JOB_RESULT_KEYS = {"items", "fields"}

# artifacts sub-keys: items, name, url, name_map, required, max_bytes,
# allowed_hosts, force_https, auth (none).
SECTION_2_10_ASYNC_JOB_ARTIFACT_KEYS = {
    "items",
    "name",
    "url",
    "name_map",
    "required",
    "max_bytes",
    "allowed_hosts",
    "force_https",
    "auth",
}

# cleanup request sub-keys: method, path, body.
SECTION_2_10_ASYNC_JOB_CLEANUP_KEYS = {"method", "path", "body"}


def test_value_forms_frozen() -> None:
    assert set(templates.VALUE_FORMS) == SECTION_2_10_VALUE_FORMS


def test_ref_roots_frozen() -> None:
    assert set(templates.REF_ROOTS) == SECTION_2_10_REF_ROOTS


def test_ref_segments_frozen() -> None:
    assert set(templates.REF_SEGMENTS) == SECTION_2_10_REF_SEGMENTS


def test_functions_frozen() -> None:
    assert set(templates.FUNCTIONS) == SECTION_2_10_FUNCTIONS


def test_generic_http_keys_frozen() -> None:
    assert set(templates.GENERIC_HTTP_KEYS) == SECTION_2_10_GENERIC_HTTP_KEYS


def test_generic_http_request_keys_frozen() -> None:
    assert set(templates.GENERIC_HTTP_REQUEST_KEYS) == SECTION_2_10_GENERIC_HTTP_REQUEST_KEYS


def test_generic_http_field_keys_frozen() -> None:
    assert set(templates.GENERIC_HTTP_FIELD_KEYS) == SECTION_2_10_GENERIC_HTTP_FIELD_KEYS


def test_box_formats_frozen() -> None:
    assert set(templates.BOX_FORMATS) == SECTION_2_10_BOX_FORMATS


def test_item_error_classes_frozen() -> None:
    assert set(templates.ITEM_ERROR_CLASSES) == SECTION_2_10_ITEM_ERROR_CLASSES


def test_error_map_classes_frozen() -> None:
    assert set(templates.ERROR_MAP_CLASSES) == SECTION_2_10_ERROR_MAP_CLASSES


def test_usage_modes_frozen() -> None:
    assert set(templates.USAGE_MODES) == SECTION_2_10_USAGE_MODES


def test_route_options_frozen() -> None:
    assert set(templates.ROUTE_OPTIONS) == SECTION_2_10_ROUTE_OPTIONS


def test_counts_match_section_2_10() -> None:
    # The inventory "Count" column: value forms 6, functions 9, generic_http
    # keys 9, async_job groups 6, route options 3.
    assert len(SECTION_2_10_VALUE_FORMS) == 6
    assert len(SECTION_2_10_FUNCTIONS) == 9
    assert len(SECTION_2_10_GENERIC_HTTP_KEYS) == 9
    assert len(SECTION_2_10_ASYNC_JOB_GROUPS) == 6
    assert len(SECTION_2_10_ROUTE_OPTIONS) == 3


def test_async_job_keys_frozen() -> None:
    assert set(templates.ASYNC_JOB_KEYS) == SECTION_2_10_ASYNC_JOB_KEYS


def test_async_job_groups_frozen() -> None:
    assert set(templates.ASYNC_JOB_GROUPS) == SECTION_2_10_ASYNC_JOB_GROUPS


def test_async_job_step_keys_frozen() -> None:
    assert set(templates.ASYNC_JOB_STEP_KEYS) == SECTION_2_10_ASYNC_JOB_STEP_KEYS


def test_async_job_auth_modes_frozen() -> None:
    assert set(templates.ASYNC_JOB_AUTH_MODES) == SECTION_2_10_ASYNC_JOB_AUTH_MODES


def test_async_job_require_predicates_frozen() -> None:
    assert (
        set(templates.ASYNC_JOB_REQUIRE_PREDICATES)
        == SECTION_2_10_ASYNC_JOB_REQUIRE_PREDICATES
    )


def test_async_job_poll_keys_frozen() -> None:
    assert set(templates.ASYNC_JOB_POLL_KEYS) == SECTION_2_10_ASYNC_JOB_POLL_KEYS


def test_async_job_backoff_keys_frozen() -> None:
    assert set(templates.ASYNC_JOB_BACKOFF_KEYS) == SECTION_2_10_ASYNC_JOB_BACKOFF_KEYS


def test_async_job_status_fields_frozen() -> None:
    assert (
        set(templates.ASYNC_JOB_STATUS_FIELDS)
        == SECTION_2_10_ASYNC_JOB_STATUS_FIELDS
    )


def test_async_job_status_states_frozen() -> None:
    assert set(templates.ASYNC_JOB_STATUS_STATES) == SECTION_2_10_ASYNC_JOB_STATUS_STATES


def test_async_job_result_keys_frozen() -> None:
    assert set(templates.ASYNC_JOB_RESULT_KEYS) == SECTION_2_10_ASYNC_JOB_RESULT_KEYS


def test_async_job_artifact_keys_frozen() -> None:
    assert set(templates.ASYNC_JOB_ARTIFACT_KEYS) == SECTION_2_10_ASYNC_JOB_ARTIFACT_KEYS


def test_async_job_cleanup_keys_frozen() -> None:
    assert set(templates.ASYNC_JOB_CLEANUP_KEYS) == SECTION_2_10_ASYNC_JOB_CLEANUP_KEYS


def test_async_job_validator_is_wired() -> None:
    # Task 16B replaced the placeholder; an empty mapping now hits the real
    # rules (steps, poll and result/artifacts are required).
    errors = templates.validate_mapping("async_job", {})
    assert "async_job mapping validation is not available yet" not in errors
    assert any("$.steps" in e for e in errors)
    assert any("$.poll" in e for e in errors)
    assert any("result" in e or "artifacts" in e for e in errors)


# ===========================================================================
# TIER 3 BLOCK (plan Section 2.10 "Tier 3 extensions (Task 28)") — LITERAL copy.
#
# Task 28 adds EXACTLY the five Tier 3 extensions to the frozen inventory, and
# nothing else. Each is one grammar element (or enum extension). The rows below
# are the literal reference the module's Tier 3 constants are pinned against; a
# code-only addition fails CI until BOTH this block and Section 2.10 change
# together. These are the rows the lead copies verbatim into Section 2.10.
#
#   | Group               | Elements (Tier 3 additions)                     | Count |
#   |---------------------|-------------------------------------------------|-------|
#   | Value forms         | `$multipart` (parts value/file/filename/        | 1     |
#   |                     |   content_type)                                 |       |
#   | `generic_http` keys | `output` (object_detection/transcription/       | 3     |
#   |                     |   caption/text_embeddings/image_embeddings);    |       |
#   |                     |   `usage_path`; `time_unit`                     |       |
#   | Box formats         | `xyxy_norm`, `xyxy_px`                           | 2     |
#   | `usage` modes       | `response_path`                                 | 1     |
#   | `async_job` keys    | `mode` (blocking, non_blocking)                 | 1     |
#   | Typed output fields | transcription (text/language/words/word/start/  | 3     |
#   |                     |   end/confidence); caption (caption);           |       |
#   |                     |   embeddings (vectors/vector)                   |       |
# ===========================================================================

# 1. Multipart value form and its part keys.
SECTION_2_10_TIER3_VALUE_FORMS = {"multipart"}
SECTION_2_10_MULTIPART_PART_KEYS = {"value", "file", "filename", "content_type"}

# 2. Tier 3 box formats (added to the accepted set).
SECTION_2_10_TIER3_BOX_FORMATS = {"xyxy_norm", "xyxy_px"}

# 3. Tier 3 usage mode.
SECTION_2_10_TIER3_USAGE_MODES = {"response_path"}

# 4. Non-blocking async mode: the `mode` key and its enum.
SECTION_2_10_ASYNC_JOB_MODE_KEY = "mode"
SECTION_2_10_ASYNC_JOB_MODES = {"blocking", "non_blocking"}

# 5. Typed generic_http output kinds and the fixed field set per kind.
SECTION_2_10_TIER3_GENERIC_HTTP_KEYS = {"output", "usage_path", "time_unit"}
SECTION_2_10_GENERIC_HTTP_OUTPUT_KINDS = {
    "object_detection",
    "transcription",
    "caption",
    "text_embeddings",
    "image_embeddings",
}
SECTION_2_10_TRANSCRIPTION_FIELD_KEYS = {
    "text",
    "language",
    "words",
    "word",
    "start",
    "end",
    "confidence",
}
SECTION_2_10_CAPTION_FIELD_KEYS = {"caption"}
SECTION_2_10_EMBEDDINGS_FIELD_KEYS = {"vectors", "vector"}
SECTION_2_10_TRANSCRIPTION_TIME_UNITS = {"seconds", "milliseconds"}


def test_tier3_value_forms_frozen() -> None:
    assert set(templates.TIER3_VALUE_FORMS) == SECTION_2_10_TIER3_VALUE_FORMS


def test_tier3_multipart_part_keys_frozen() -> None:
    assert set(templates.MULTIPART_PART_KEYS) == SECTION_2_10_MULTIPART_PART_KEYS


def test_tier3_box_formats_frozen() -> None:
    assert set(templates.TIER3_BOX_FORMATS) == SECTION_2_10_TIER3_BOX_FORMATS


def test_accepted_box_formats_is_tier1_plus_tier3() -> None:
    assert set(templates.ACCEPTED_BOX_FORMATS) == (
        SECTION_2_10_BOX_FORMATS | SECTION_2_10_TIER3_BOX_FORMATS
    )


def test_tier3_usage_modes_frozen() -> None:
    assert set(templates.TIER3_USAGE_MODES) == SECTION_2_10_TIER3_USAGE_MODES


def test_accepted_usage_modes_is_tier1_plus_tier3() -> None:
    assert set(templates.ACCEPTED_USAGE_MODES) == (
        SECTION_2_10_USAGE_MODES | SECTION_2_10_TIER3_USAGE_MODES
    )


def test_tier3_async_job_mode_frozen() -> None:
    assert templates.ASYNC_JOB_MODE_KEY == SECTION_2_10_ASYNC_JOB_MODE_KEY
    assert set(templates.ASYNC_JOB_MODES) == SECTION_2_10_ASYNC_JOB_MODES


def test_accepted_async_job_keys_is_tier1_plus_mode() -> None:
    assert set(templates.ACCEPTED_ASYNC_JOB_KEYS) == (
        SECTION_2_10_ASYNC_JOB_KEYS | {SECTION_2_10_ASYNC_JOB_MODE_KEY}
    )


def test_tier3_generic_http_keys_frozen() -> None:
    assert set(templates.TIER3_GENERIC_HTTP_KEYS) == SECTION_2_10_TIER3_GENERIC_HTTP_KEYS


def test_accepted_generic_http_keys_is_tier1_plus_tier3() -> None:
    assert set(templates.ACCEPTED_GENERIC_HTTP_KEYS) == (
        SECTION_2_10_GENERIC_HTTP_KEYS | SECTION_2_10_TIER3_GENERIC_HTTP_KEYS
    )


def test_tier3_output_kinds_frozen() -> None:
    assert set(templates.GENERIC_HTTP_OUTPUT_KINDS) == SECTION_2_10_GENERIC_HTTP_OUTPUT_KINDS


def test_tier3_transcription_field_keys_frozen() -> None:
    assert set(templates.TRANSCRIPTION_FIELD_KEYS) == SECTION_2_10_TRANSCRIPTION_FIELD_KEYS


def test_tier3_caption_field_keys_frozen() -> None:
    assert set(templates.CAPTION_FIELD_KEYS) == SECTION_2_10_CAPTION_FIELD_KEYS


def test_tier3_embeddings_field_keys_frozen() -> None:
    assert set(templates.EMBEDDINGS_FIELD_KEYS) == SECTION_2_10_EMBEDDINGS_FIELD_KEYS


def test_tier3_transcription_time_units_frozen() -> None:
    assert set(templates.TRANSCRIPTION_TIME_UNITS) == SECTION_2_10_TRANSCRIPTION_TIME_UNITS


def test_tier3_counts_match_section_2_10() -> None:
    # Tier 3 "Count" column: value forms +1, generic_http keys +3, box formats
    # +2, usage modes +1, async_job keys +1, typed output field groups 3.
    assert len(SECTION_2_10_TIER3_VALUE_FORMS) == 1
    assert len(SECTION_2_10_TIER3_GENERIC_HTTP_KEYS) == 3
    assert len(SECTION_2_10_TIER3_BOX_FORMATS) == 2
    assert len(SECTION_2_10_TIER3_USAGE_MODES) == 1
    assert len(SECTION_2_10_ASYNC_JOB_MODES) == 2
    assert len(SECTION_2_10_GENERIC_HTTP_OUTPUT_KINDS) == 5
