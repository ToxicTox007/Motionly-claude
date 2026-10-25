"""Tests for the ``generic_http`` adapter — Tier 1 slice (plan Task 15).

Two differently-shaped fake synchronous object-detection APIs prove that the
adapter is shape-independent and config-driven:

* ``NESTED_MAPPING`` — a per-image ``responses`` list, each image carrying a
  nested ``localizedObjects`` list with ``vertices_norm`` boxes and an optional
  per-image ``error.code`` (the archived "objects" shape, neutral field names).
* ``FLAT_MAPPING`` — a flat ``predictions`` list, one entry per image, with
  ``xywh_norm`` boxes and different field names (``tag`` / ``confidence`` /
  ``bbox``) and a different error field (``status``).

The acceptance test drives ``ObjectDetectionRequest`` through a real
:class:`ModelGateway` with only a mapping dict and a fake server, and checks the
output matches the normalized detection schema. No vendor names appear here.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.services.generic_http_adapter import (
    GenericHttpAdapter,
    GenericHttpRouteConfig,
)
from app.services.inference_http import NoAuth
from app.services.model_gateway import (
    AdapterResult,
    InMemoryBudgetLedger,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelProvidersExhausted,
    ModelRoute,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
)
from app.services.provider_resilience import ProviderCallFailure

JPEG = b"\xff\xd8\xff\xe0jpeg"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00"


# ---------------------------------------------------------------------------
# Mappings (the only thing that differs between the two providers).
# ---------------------------------------------------------------------------
NESTED_MAPPING = {
    "request": {
        "method": "POST",
        "path": "/v1/detect",
        "body": {
            "images": {
                "$map": {
                    "over": "input.images",
                    "each": {"content": {"$fn": "base64", "args": [{"$ref": "item"}]}},
                }
            },
            "max_results": {"$ref": "input.max_results"},
        },
    },
    "items": "response.responses",
    "aligned_to_input": True,
    "fields": {
        "detections": "localizedObjects",
        "label": "name",
        "score": "score",
        "box": "boundingPoly.vertices",
    },
    "box_format": "vertices_norm",
    "item_error": "error.code",
    "item_error_codes": {"3": "rejected", "9": "rejected", "11": "rejected"},
    "error_map": {"400": "rejected", "401": "auth", "403": "auth", "429": "quota"},
    "usage": "count_ok_items",
}

FLAT_MAPPING = {
    "request": {
        "method": "POST",
        "path": "/detect-flat",
        "body": {
            "frames": {
                "$map": {
                    "over": "input.images",
                    "each": {"$fn": "data_url", "args": [{"$ref": "item"}]},
                }
            }
        },
    },
    "items": "response.predictions",
    "aligned_to_input": True,
    # A flat shape: each prediction *is* one detection (no nested list).
    "fields": {
        "label": "tag",
        "score": "confidence",
        "box": "bbox",
    },
    "box_format": "xywh_norm",
    "item_error": "status",
    "item_error_codes": {"bad_input": "rejected"},
    "error_map": {"400": "rejected", "503": "unavailable"},
    "usage": "count_items",
}

ALIASES = {"mobile phone": "cell phone", "television": "tv"}


def _nested_annotation(name, score, vertices):
    return {"name": name, "score": score, "boundingPoly": {"vertices": vertices}}


LAPTOP = _nested_annotation(
    "Laptop",
    0.91,
    [{"x": 0.1, "y": 0.2}, {"x": 0.6, "y": 0.2}, {"x": 0.6, "y": 0.7}, {"x": 0.1, "y": 0.7}],
)
PHONE = _nested_annotation(
    "Mobile phone", 0.55, [{}, {"x": 0.25}, {"x": 0.25, "y": 0.5}, {"y": 0.5}]
)


# ---------------------------------------------------------------------------
# Fake servers (httpx.MockTransport handlers; no sockets).
# ---------------------------------------------------------------------------
class NestedServer:
    def __init__(self, responder=None, status=200):
        self.requests: list[httpx.Request] = []
        self.status = status
        self.responder = responder or (
            lambda count: [{"localizedObjects": [LAPTOP, PHONE]} for _ in range(count)]
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"status": self.status}})
        count = len(json.loads(request.content)["images"])
        return httpx.Response(200, json={"responses": self.responder(count)})


class FlatServer:
    def __init__(self, responder=None, status=200):
        self.requests: list[httpx.Request] = []
        self.status = status
        self.responder = responder or (
            lambda count: [
                {"tag": "Cat", "confidence": 0.8, "bbox": [0.1, 0.1, 0.2, 0.2]}
                for _ in range(count)
            ]
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"status": self.status}})
        count = len(json.loads(request.content)["frames"])
        return httpx.Response(200, json={"predictions": self.responder(count)})


def _nested_adapter(server, *, mapping=None, **config_kwargs):
    config_kwargs.setdefault("max_batch", 16)
    config = GenericHttpRouteConfig(
        mapping=mapping or NESTED_MAPPING,
        label_aliases=ALIASES,
        **config_kwargs,
    )
    return GenericHttpAdapter(
        base_url="https://detect.example.invalid",
        auth=NoAuth(),
        timeout_s=30.0,
        provider="objects-endpoint",
        transport=httpx.MockTransport(server.handler),
        routes={"objects-remote": config},
    )


def _flat_adapter(server, **config_kwargs):
    config_kwargs.setdefault("max_batch", 16)
    config = GenericHttpRouteConfig(
        mapping=FLAT_MAPPING, label_aliases=ALIASES, **config_kwargs
    )
    return GenericHttpAdapter(
        base_url="https://flat.example.invalid",
        auth=NoAuth(),
        timeout_s=30.0,
        provider="objects-flat-endpoint",
        transport=httpx.MockTransport(server.handler),
        routes={"objects-remote": config},
    )


def _route(route_id="objects-remote", adapter="objects-endpoint"):
    return ModelRoute(
        route_id=route_id,
        capability=ModelCapability.VISION,
        adapter=adapter,
        provider="objects-endpoint",
        model="example/detector:v1",
        model_revision="builtin-stable-2026-10-22",
        operations=(ModelOperation.VISION_OBJECT_DETECTION,),
        input_cost_per_million_usd=1500.0,
        endpoint_host="detect.example.invalid",
    )


# ---------------------------------------------------------------------------
# Request shaping / value forms end to end.
# ---------------------------------------------------------------------------
def test_request_body_is_built_by_the_template() -> None:
    server = NestedServer()
    adapter = _nested_adapter(server)

    adapter.invoke(
        route=_route(),
        request=ObjectDetectionRequest(images=(JPEG, PNG), max_results=12),
    )

    (request,) = server.requests
    assert str(request.url) == "https://detect.example.invalid/v1/detect"
    body = json.loads(request.content)
    assert body["max_results"] == 12
    assert [base64.b64decode(img["content"]) for img in body["images"]] == [JPEG, PNG]
    # accept header is always sent; no auth header with NoAuth.
    assert request.headers["accept"] == "application/json"
    assert "authorization" not in request.headers


def test_data_url_value_form_in_flat_shape() -> None:
    server = FlatServer()
    adapter = _flat_adapter(server)

    adapter.invoke(route=_route(), request=ObjectDetectionRequest(images=(PNG,)))

    body = json.loads(server.requests[0].content)
    assert body["frames"][0].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# Both box formats and sorting.
# ---------------------------------------------------------------------------
def test_nested_vertices_boxes_sorted_and_aliased() -> None:
    result = _nested_adapter(NestedServer()).invoke(
        route=_route(), request=ObjectDetectionRequest(images=(JPEG,))
    )
    assert isinstance(result.output, ObjectDetectionOutput)
    (detections,) = result.output.detections
    laptop, phone = detections
    assert (laptop.label, laptop.confidence) == ("laptop", 0.91)
    assert (laptop.x, laptop.y, laptop.width, laptop.height) == (0.1, 0.2, 0.5, 0.5)
    assert phone.label == "cell phone"  # aliased
    assert (phone.x, phone.y, phone.width, phone.height) == (0.0, 0.0, 0.25, 0.5)


def test_flat_xywh_boxes() -> None:
    result = _flat_adapter(FlatServer()).invoke(
        route=_route(), request=ObjectDetectionRequest(images=(JPEG, PNG))
    )
    assert len(result.output.detections) == 2
    (one,) = result.output.detections[0]
    assert one.label == "cat"
    assert (one.x, one.y, one.width, one.height) == (0.1, 0.1, 0.2, 0.2)


# ---------------------------------------------------------------------------
# Item errors: partial and total.
# ---------------------------------------------------------------------------
def test_partial_item_errors_return_empty_and_bill_only_ok_items() -> None:
    def responder(count):
        return [{"error": {"code": 3}}] + [
            {"localizedObjects": [LAPTOP]} for _ in range(count - 1)
        ]

    result = _nested_adapter(NestedServer(responder)).invoke(
        route=_route(), request=ObjectDetectionRequest(images=(JPEG, JPEG, JPEG))
    )
    assert result.output.detections[0] == ()
    assert len(result.output.detections[1]) == 1
    # usage: count_ok_items -> 2 of 3 images were annotated.
    assert result.usage.input_units == 2


@pytest.mark.parametrize(
    ("code", "expected"),
    [(3, "provider_request_rejected"), (13, "provider_unavailable")],
)
def test_all_item_errors_raise_that_class(code, expected) -> None:
    server = NestedServer(lambda count: [{"error": {"code": code}} for _ in range(count)])
    with pytest.raises(ProviderCallFailure) as raised:
        _nested_adapter(server).invoke(
            route=_route(), request=ObjectDetectionRequest(images=(JPEG, JPEG))
        )
    assert raised.value.error_info.code == expected
    # Rejected is not billable/fallback; unavailable is retryable.
    if code == 3:
        assert raised.value.error_info.billable is False
        assert raised.value.error_info.can_fallback is False
    else:
        assert raised.value.error_info.retryable is True


def test_flat_item_error_field() -> None:
    server = FlatServer(
        lambda count: [{"status": "bad_input"}]
        + [{"tag": "cat", "confidence": 0.7, "bbox": [0, 0, 0.1, 0.1]} for _ in range(count - 1)]
    )
    result = _flat_adapter(server).invoke(
        route=_route(), request=ObjectDetectionRequest(images=(JPEG, JPEG))
    )
    assert result.output.detections[0] == ()
    assert len(result.output.detections[1]) == 1
    assert result.usage.input_units == 2  # count_items


# ---------------------------------------------------------------------------
# aligned_to_input, oversized batch, oversized response, error map.
# ---------------------------------------------------------------------------
def test_length_mismatch_is_malformed() -> None:
    server = NestedServer(lambda count: [{"localizedObjects": []}])  # only one response
    with pytest.raises(ProviderCallFailure) as raised:
        _nested_adapter(server).invoke(
            route=_route(), request=ObjectDetectionRequest(images=(JPEG, JPEG))
        )
    assert raised.value.error_info.code == "provider_malformed_response"


def test_oversized_batch_rejected_before_send() -> None:
    server = NestedServer()
    adapter = _nested_adapter(server, max_batch=2)
    with pytest.raises(ProviderCallFailure) as raised:
        adapter.invoke(route=_route(), request=ObjectDetectionRequest(images=(JPEG, JPEG, JPEG)))
    assert raised.value.error_info.code == "provider_request_rejected"
    assert server.requests == []


def test_oversized_response_is_malformed() -> None:
    big = [{"localizedObjects": [LAPTOP] * 2000} for _ in range(1)]
    server = NestedServer(lambda count: big * count)
    adapter = _nested_adapter(server, max_request_bytes=200)
    with pytest.raises(ProviderCallFailure) as raised:
        adapter.invoke(route=_route(), request=ObjectDetectionRequest(images=(JPEG,)))
    assert raised.value.error_info.code == "provider_malformed_response"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "provider_request_rejected"),
        (401, "provider_auth_failed"),
        (403, "provider_auth_failed"),
        (429, "provider_quota_exceeded"),
    ],
)
def test_error_map_classifies_http_status(status, code) -> None:
    server = NestedServer(status=status)
    with pytest.raises(ProviderCallFailure) as raised:
        _nested_adapter(server).invoke(
            route=_route(), request=ObjectDetectionRequest(images=(JPEG,))
        )
    assert raised.value.error_info.code == code


def test_status_not_in_error_map_falls_back_to_classifier() -> None:
    # 500 is not in NESTED_MAPPING's error_map -> classify_provider_status(5xx).
    server = NestedServer(status=503)
    with pytest.raises(ProviderCallFailure) as raised:
        _nested_adapter(server).invoke(
            route=_route(), request=ObjectDetectionRequest(images=(JPEG,))
        )
    assert raised.value.error_info.code == "provider_unavailable"


def test_unknown_route_raises() -> None:
    adapter = _nested_adapter(NestedServer())
    with pytest.raises(ValueError, match="no mapping for route"):
        adapter.invoke(
            route=_route(route_id="not-configured"),
            request=ObjectDetectionRequest(images=(JPEG,)),
        )


def test_non_object_detection_request_rejected() -> None:
    from pathlib import Path

    from app.services.model_gateway import DiarizationRequest

    adapter = _nested_adapter(NestedServer())
    with pytest.raises(TypeError, match="ObjectDetectionRequest"):
        adapter.invoke(
            route=_route(),
            request=DiarizationRequest(audio_path=Path("/tmp/a.wav"), duration_s=1.0),
        )


def test_invalid_mapping_fails_at_construction() -> None:
    with pytest.raises(ValueError, match="mapping is invalid"):
        GenericHttpRouteConfig(mapping={"request": {"method": "POST", "path": "/x"}})


# ---------------------------------------------------------------------------
# Acceptance: config-only through a real ModelGateway, for BOTH shapes.
# ---------------------------------------------------------------------------
def test_acceptance_nested_shape_through_real_gateway() -> None:
    server = NestedServer()
    adapter = _nested_adapter(server)
    ledger = InMemoryBudgetLedger()
    gateway = ModelGateway(
        adapters={"objects-endpoint": adapter},
        routes=[_route()],
        budget_ledger=ledger,
    )

    out = gateway.invoke(
        ObjectDetectionRequest(images=(JPEG, JPEG)),
        context=ModelCallContext(workspace_id="ws-1"),
    )

    assert isinstance(out.output, ObjectDetectionOutput)
    assert len(out.output.detections) == 2
    assert [d.label for d in out.output.detections[0]] == ["laptop", "cell phone"]
    # count_ok_items: both images annotated -> 2 units priced.
    assert out.metadata.usage.input_units == 2
    assert out.metadata.actual_cost_usd == pytest.approx(2 * 1500.0 / 1_000_000)


def test_acceptance_flat_shape_through_real_gateway() -> None:
    server = FlatServer()
    adapter = _flat_adapter(server)
    gateway = ModelGateway(
        adapters={"objects-flat-endpoint": adapter},
        routes=[_route(adapter="objects-flat-endpoint")],
    )

    out = gateway.invoke(ObjectDetectionRequest(images=(JPEG, JPEG, JPEG)))

    assert isinstance(out.output, ObjectDetectionOutput)
    assert len(out.output.detections) == 3
    assert all(d[0].label == "cat" for d in out.output.detections)
    assert out.metadata.usage.input_units == 3  # count_items


def test_total_failure_propagates_through_gateway() -> None:
    server = NestedServer(lambda count: [{"error": {"code": 3}} for _ in range(count)])
    adapter = _nested_adapter(server)
    gateway = ModelGateway(adapters={"objects-endpoint": adapter}, routes=[_route()])

    with pytest.raises(ModelProvidersExhausted) as raised:
        gateway.invoke(ObjectDetectionRequest(images=(JPEG,)))
    assert raised.value.failures[0]["code"] == "provider_request_rejected"


def test_adapter_result_type() -> None:
    result = _nested_adapter(NestedServer()).invoke(
        route=_route(), request=ObjectDetectionRequest(images=(JPEG,))
    )
    assert isinstance(result, AdapterResult)
    assert result.usage_reported is True
