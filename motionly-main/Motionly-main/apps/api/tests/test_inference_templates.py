"""Tests for the Tier 1 grammar engine (plan Task 15, Section 2.10).

Covers every value form and function (happy and failure paths), request-path
placeholders and their hard rules, and rejection of every disallowed construct
at validation time.
"""

from __future__ import annotations

import base64
import re

import pytest

from app.services.inference_templates import (
    FUNCTIONS,
    TemplateContext,
    TemplateError,
    TemplateEvaluationError,
    TemplateMissing,
    evaluate_value,
    render_path,
    resolve_ref,
    sniff_image_mime,
    validate_mapping,
)

PNG = b"\x89PNG\r\n\x1a\n\x00\x00"
JPEG = b"\xff\xd8\xff\xe0jpeg"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "
GIF = b"GIF89a\x00\x00"


def _ctx(**kwargs) -> TemplateContext:
    return TemplateContext(**kwargs)


# -- value forms ---------------------------------------------------------------
def test_json_literal_passthrough() -> None:
    assert evaluate_value({"a": 1, "b": [2, "three"]}, _ctx()) == {"a": 1, "b": [2, "three"]}
    assert evaluate_value(42, _ctx()) == 42


def test_ref_reads_roots() -> None:
    ctx = _ctx(
        input={"max_results": 12, "images": [b"a", b"b"]},
        params={"model": "m1"},
        vars={"name": "media://x"},
        response={"results": [{"score": 0.5}]},
    )
    assert evaluate_value({"$ref": "input.max_results"}, ctx) == 12
    assert evaluate_value({"$ref": "params.model"}, ctx) == "m1"
    assert evaluate_value({"$ref": "vars.name"}, ctx) == "media://x"
    assert evaluate_value({"$ref": "response.results[0].score"}, ctx) == 0.5


def test_ref_index_and_wildcard() -> None:
    ctx = _ctx(response={"items": [{"n": 1}, {"n": 2}, {"n": 3}]})
    assert resolve_ref("response.items[1].n", ctx) == 2
    assert resolve_ref("response.items[*].n", ctx) == [1, 2, 3]


def test_ref_variable_segment() -> None:
    ctx = _ctx(response={"job-123": {"status": "done"}}, vars={"job_id": "job-123"})
    assert resolve_ref("response.{vars.job_id}.status", ctx) == "done"


def test_ref_item_root_requires_item() -> None:
    with pytest.raises(TemplateMissing):
        resolve_ref("item.label", _ctx())
    assert resolve_ref("item.label", _ctx().with_item({"label": "cat"})) == "cat"


def test_ref_missing_raises_template_missing() -> None:
    with pytest.raises(TemplateMissing):
        resolve_ref("response.absent", _ctx(response={}))
    with pytest.raises(TemplateMissing):
        resolve_ref("response.list[5]", _ctx(response={"list": [1]}))


def test_map_over_bounded_by_max_batch() -> None:
    ctx = _ctx(input={"images": [PNG, JPEG]}, max_batch=16)
    result = evaluate_value(
        {"$map": {"over": "input.images", "each": {"$fn": "base64", "args": [{"$ref": "item"}]}}},
        ctx,
    )
    assert result == [base64.b64encode(PNG).decode(), base64.b64encode(JPEG).decode()]


def test_map_exceeding_max_batch_rejected() -> None:
    ctx = _ctx(input={"images": [PNG, JPEG, GIF]}, max_batch=2)
    with pytest.raises(TemplateError, match="exceeds max_batch"):
        evaluate_value({"$map": {"over": "input.images", "each": {"$ref": "item"}}}, ctx)


# -- functions -----------------------------------------------------------------
def test_base64() -> None:
    assert evaluate_value({"$fn": "base64", "args": [JPEG]}, _ctx()) == base64.b64encode(
        JPEG
    ).decode()


def test_base64_requires_bytes() -> None:
    with pytest.raises(TemplateEvaluationError):
        evaluate_value({"$fn": "base64", "args": ["not-bytes"]}, _ctx())


def test_data_url_sniffs_each_format() -> None:
    cases = (
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (WEBP, "image/webp"),
        (GIF, "image/gif"),
    )
    for data, mime in cases:
        url = evaluate_value({"$fn": "data_url", "args": [data]}, _ctx())
        assert url.startswith(f"data:{mime};base64,")


def test_data_url_rejects_unknown_format() -> None:
    with pytest.raises(TemplateEvaluationError):
        evaluate_value({"$fn": "data_url", "args": [b"\x00\x01bogus"]}, _ctx())


def test_uuid_is_random_uuid() -> None:
    value = evaluate_value({"$fn": "uuid", "args": []}, _ctx())
    assert re.fullmatch(r"[0-9a-f-]{36}", value)
    assert value != evaluate_value({"$fn": "uuid", "args": []}, _ctx())


def test_concat_builds_opaque_identifier() -> None:
    ctx = _ctx()
    result = evaluate_value(
        {"$fn": "concat", "args": ["media://motionly-", {"$fn": "uuid", "args": []}]}, ctx
    )
    assert result.startswith("media://motionly-")


def test_concat_rejects_non_scalar() -> None:
    with pytest.raises(TemplateEvaluationError):
        evaluate_value({"$fn": "concat", "args": [{"$ref": "input.x"}]}, _ctx(input={"x": [1]}))
    with pytest.raises(TemplateEvaluationError):
        evaluate_value({"$fn": "concat", "args": [True]}, _ctx())


def test_to_int_and_failure() -> None:
    assert evaluate_value({"$fn": "to_int", "args": ["42"]}, _ctx()) == 42
    with pytest.raises(TemplateEvaluationError):
        evaluate_value({"$fn": "to_int", "args": ["x"]}, _ctx())


def test_to_float_and_failure() -> None:
    assert evaluate_value({"$fn": "to_float", "args": ["1.5"]}, _ctx()) == 1.5
    with pytest.raises(TemplateEvaluationError):
        evaluate_value({"$fn": "to_float", "args": ["nope"]}, _ctx())


def test_to_ms_rounds_like_archived_adapter() -> None:
    # round(seconds * 1000) — parity with the archived diarization adapter.
    assert evaluate_value({"$fn": "to_ms", "args": [1.2345]}, _ctx()) == 1234
    assert evaluate_value({"$fn": "to_ms", "args": [1.2346]}, _ctx()) == 1235
    assert evaluate_value({"$fn": "to_ms", "args": [0.5]}, _ctx()) == 500


def test_clamp01() -> None:
    assert evaluate_value({"$fn": "clamp01", "args": [1.7]}, _ctx()) == 1.0
    assert evaluate_value({"$fn": "clamp01", "args": [-3]}, _ctx()) == 0.0


def test_default_uses_fallback_only_when_missing_or_null() -> None:
    ctx = _ctx(response={"present": 7, "null_value": None})
    assert evaluate_value(
        {"$fn": "default", "args": [{"$ref": "response.present"}, 0]}, ctx
    ) == 7
    assert evaluate_value(
        {"$fn": "default", "args": [{"$ref": "response.absent"}, "fb"]}, ctx
    ) == "fb"
    assert evaluate_value(
        {"$fn": "default", "args": [{"$ref": "response.null_value"}, "fb"]}, ctx
    ) == "fb"


def test_unknown_function_rejected_at_eval() -> None:
    with pytest.raises(TemplateError, match="unknown function"):
        evaluate_value({"$fn": "exec", "args": []}, _ctx())


# -- request-path placeholders -------------------------------------------------
def test_render_path_percent_encodes() -> None:
    ctx = _ctx(vars={"job_id": "a b/c"})
    assert render_path("/jobs/{vars.job_id}", ctx) == "/jobs/a%20b%2Fc"


def test_render_path_roots() -> None:
    ctx = _ctx(input={"x": "1"}, params={"p": "2"}, vars={"v": "3"})
    assert render_path("/a/{input.x}/{params.p}/{vars.v}", ctx) == "/a/1/2/3"


def test_render_path_rejects_scheme_and_double_slash() -> None:
    with pytest.raises(TemplateError):
        render_path("https://evil/{vars.x}", _ctx(vars={"x": "1"}))
    with pytest.raises(TemplateError):
        render_path("//evil/path", _ctx())
    with pytest.raises(TemplateError):
        render_path("relative/path", _ctx())


def test_render_path_blocks_traversal_after_substitution() -> None:
    # A substituted value cannot inject a '..' segment because '/' is encoded.
    rendered = render_path("/x/{vars.seg}", _ctx(vars={"seg": "../etc"}))
    assert rendered == "/x/..%2Fetc"
    # A literal traversal in the template is rejected statically.
    with pytest.raises(TemplateError):
        render_path("/x/../y", _ctx())


def test_render_path_response_root_not_allowed() -> None:
    with pytest.raises(TemplateError):
        render_path("/x/{response.id}", _ctx(response={"id": "1"}))


def test_sniff_image_mime() -> None:
    assert sniff_image_mime(PNG) == "image/png"
    assert sniff_image_mime(JPEG) == "image/jpeg"
    assert sniff_image_mime(WEBP) == "image/webp"
    assert sniff_image_mime(GIF) == "image/gif"
    assert sniff_image_mime(b"\x00bogus") is None


# -- validation: disallowed constructs ----------------------------------------
_BASE_MAPPING = {
    "request": {"method": "POST", "path": "/v1/detect", "body": {}},
    "items": "response.results",
    "fields": {"detections": "objects", "label": "name", "score": "score", "box": "box"},
    "box_format": "vertices_norm",
}


def test_valid_mapping_has_no_errors() -> None:
    assert validate_mapping("generic_http", _BASE_MAPPING) == []


def test_reject_unknown_top_level_key() -> None:
    bad = {**_BASE_MAPPING, "surprise": 1}
    errors = validate_mapping("generic_http", bad)
    assert any("unknown generic_http keys" in e for e in errors)


def test_reject_unknown_function() -> None:
    bad = {
        **_BASE_MAPPING,
        "request": {"method": "POST", "path": "/x", "body": {"$fn": "spawn", "args": []}},
    }
    errors = validate_mapping("generic_http", bad)
    assert any("unknown function" in e for e in errors)


def test_reject_env_or_secret_root() -> None:
    bad = {
        **_BASE_MAPPING,
        "request": {"method": "POST", "path": "/x", "body": {"k": {"$ref": "env.API_KEY"}}},
    }
    errors = validate_mapping("generic_http", bad)
    assert any("ref root" in e for e in errors)


def test_reject_absolute_url_path() -> None:
    bad = {**_BASE_MAPPING, "request": {"method": "POST", "path": "https://evil/x", "body": {}}}
    errors = validate_mapping("generic_http", bad)
    assert any("absolute URL or contain a scheme" in e for e in errors)


def test_reject_host_changing_placeholder() -> None:
    bad = {
        **_BASE_MAPPING,
        "request": {"method": "POST", "path": "/x/{response.host}", "body": {}},
    }
    errors = validate_mapping("generic_http", bad)
    assert any("placeholder root" in e for e in errors)


def test_reject_traversal_path() -> None:
    bad = {**_BASE_MAPPING, "request": {"method": "POST", "path": "/x/../y", "body": {}}}
    errors = validate_mapping("generic_http", bad)
    assert any("'..'" in e for e in errors)


def test_reject_map_without_bound_is_runtime_but_static_checks_shape() -> None:
    # $map is always bounded at eval time by max_batch; statically we only reject
    # an ill-formed $map (missing over/each or extra keys).
    bad = {
        **_BASE_MAPPING,
        "request": {
            "method": "POST",
            "path": "/x",
            "body": {"m": {"$map": {"over": "input.images"}}},
        },
    }
    errors = validate_mapping("generic_http", bad)
    assert any("$map requires 'each'" in e for e in errors)


def test_reject_bad_box_format_and_usage_and_classes() -> None:
    bad = {
        **_BASE_MAPPING,
        "box_format": "pixels_absolute",  # not a known box format in any tier
        "usage": "count_everything",
        "item_error_codes": {"3": "fatal"},
        "error_map": {"400": "boom", "9": "rejected"},
    }
    errors = validate_mapping("generic_http", bad)
    joined = " | ".join(errors)
    assert "box_format" in joined
    assert "usage" in joined
    assert "item_error_codes" in joined
    assert "error_map" in joined


def test_reject_unknown_field_key() -> None:
    bad = {**_BASE_MAPPING, "fields": {"label": "name", "mystery": "x"}}
    errors = validate_mapping("generic_http", bad)
    assert any("unknown field keys" in e for e in errors)


def test_reject_bad_method() -> None:
    bad = {**_BASE_MAPPING, "request": {"method": "TRACE", "path": "/x", "body": {}}}
    errors = validate_mapping("generic_http", bad)
    assert any("request.method" in e for e in errors)


def test_async_job_validation_is_available() -> None:
    # Task 16B implemented the async_job validator. An empty mapping now fails
    # the real rules (steps, poll and result/artifacts are all required) rather
    # than returning the old "not available yet" placeholder.
    errors = validate_mapping("async_job", {})
    assert "async_job mapping validation is not available yet" not in errors
    assert any("$.steps" in e for e in errors)
    assert any("$.poll" in e for e in errors)


def test_unknown_adapter_type() -> None:
    errors = validate_mapping("mystery", {})
    assert errors == ["$: unknown adapter type 'mystery'"]


def test_function_list_is_complete() -> None:
    assert set(FUNCTIONS) == {
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
