"""Task 6: the CloudFront edge contract (infra/aws/edge-contract.json).

The contract is the single description of the edge; the CPU-stage CDN simulator
and the CloudFormation template are both generated from or checked against it.
These tests pin the properties the application depends on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "infra" / "aws" / "edge-contract.json"
ROUTES = ROOT / "infra" / "Caddyfile.routes"
NEXT_CONFIG = ROOT / "apps" / "web" / "next.config.ts"

ALL_METHODS = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
# AWS managed policy IDs, from the CloudFront Developer Guide ("Use managed
# cache policies", "Use managed origin request policies"), checked 2026-10-07.
MANAGED_POLICY_IDS = {
    "Managed-CachingDisabled": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
    "Managed-CachingOptimized": "658327ea-f89d-4fab-a63d-7e88639e58f6",
    "Managed-AllViewerAndCloudFrontHeaders-2022-06": "33f36d7e-f396-46d9-90e0-52428a34d9dc",
    "Managed-HostHeaderOnly": "bf0718e1-ba1e-49d1-88b1-f726733018ae",
}
# Status codes CloudFront lets a distribution configure error caching for.
CONFIGURABLE_ERROR_CODES = [400, 403, 404, 405, 414, 416, 500, 501, 502, 503, 504]
# Response headers CloudFront refuses to remove (response headers policies).
UNREMOVABLE_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "expect",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "via",
    "warning",
    "x-cache",
    "x-forwarded-proto",
    "x-real-ip",
}
# Only public, content-addressed (or explicitly short-lived) assets are cached.
CACHEABLE_PATHS = {"/_next/static/*", "/icon.svg"}
PATH_PATTERN = re.compile(r"^(\*|/[A-Za-z0-9_\-.*$/~\"'@:+?&=]*)$")


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _behaviors(contract: dict) -> list[dict]:
    return contract["behaviors"]


def _behavior(contract: dict, pattern: str) -> dict:
    matches = [item for item in _behaviors(contract) if item["path_pattern"] == pattern]
    assert len(matches) == 1, f"expected one behavior for {pattern}"
    return matches[0]


def _cache_policy(contract: dict, behavior: dict) -> dict:
    return contract["cache_policies"][behavior["cache_policy"]]


def _origin_request_policy(contract: dict, behavior: dict) -> dict:
    return contract["origin_request_policies"][behavior["origin_request_policy"]]


def _caches(contract: dict, behavior: dict) -> bool:
    return _cache_policy(contract, behavior)["max_ttl"] > 0


def _ingress_prefixes() -> list[str]:
    code = "\n".join(
        line
        for line in ROUTES.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )
    return re.findall(r"^handle_path (/[^\s{]+) \{", code, flags=re.MULTILINE)


def matches(pattern: str, path: str) -> bool:
    """CloudFront path-pattern semantics: case-sensitive, '*' = any characters
    (including none), '?' = exactly one character."""
    if pattern == "*":
        return True
    expression = "".join(
        ".*" if char == "*" else "." if char == "?" else re.escape(char) for char in pattern
    )
    return re.fullmatch(expression, path) is not None


def first_matching_behavior(contract: dict, path: str) -> dict:
    return next(item for item in _behaviors(contract) if matches(item["path_pattern"], path))


def test_behaviors_are_valid_ordered_and_end_with_the_default(contract: dict) -> None:
    patterns = [item["path_pattern"] for item in _behaviors(contract)]

    assert len(patterns) == len(set(patterns)), patterns
    assert patterns[-1] == "*" and patterns.count("*") == 1
    for pattern in patterns:
        assert len(pattern) <= 255 and PATH_PATTERN.fullmatch(pattern), pattern
    # No earlier pattern shadows a later one completely.
    for later in patterns[:-1]:
        probe = later.replace("*", "x").replace("?", "x")
        assert first_matching_behavior(contract, probe)["path_pattern"] == later, later


def test_every_behavior_redirects_viewers_to_https(contract: dict) -> None:
    for behavior in _behaviors(contract):
        assert behavior["viewer_protocol_policy"] == "redirect-to-https", behavior["path_pattern"]
        assert set(behavior["cached_methods"]) == {"GET", "HEAD"}
        assert set(behavior["cached_methods"]) <= set(behavior["allowed_methods"])


def test_only_public_immutable_assets_are_cached(contract: dict) -> None:
    cached = {item["path_pattern"] for item in _behaviors(contract) if _caches(contract, item)}

    assert cached == CACHEABLE_PATHS
    assert (ROOT / "apps" / "web" / "public" / "icon.svg").exists()
    for pattern in cached:
        behavior = _behavior(contract, pattern)
        forwarded = _origin_request_policy(contract, behavior)
        key = _cache_policy(contract, behavior)["cache_key"]
        # A cached response must never depend on who asked for it.
        assert behavior["allowed_methods"] == ["GET", "HEAD"], pattern
        assert key == {"headers": [], "cookies": "none", "query_strings": "none"}, pattern
        assert forwarded["cookies"] == "none" and forwarded["query_strings"] == "none"
        assert forwarded["forwards_authorization"] is False, pattern


@pytest.mark.parametrize(
    "path", ["/api/projects", "/supabase/auth/v1/token", "/pay/checkout/order_1", "/dashboard"]
)
def test_dynamic_paths_reach_the_origin_uncached_with_the_whole_request(
    contract: dict, path: str
) -> None:
    behavior = first_matching_behavior(contract, path)
    forwarded = _origin_request_policy(contract, behavior)

    assert not _caches(contract, behavior), path
    assert behavior["allowed_methods"] == ALL_METHODS, path
    assert forwarded["headers"] == "all-viewer"
    assert forwarded["cookies"] == "all" and forwarded["query_strings"] == "all"
    # Authorization reaches the origin only because caching is disabled.
    assert forwarded["forwards_authorization"] is True


def test_every_ingress_route_has_its_own_uncached_behavior(contract: dict) -> None:
    prefixes = _ingress_prefixes()

    assert prefixes == ["/api/*", "/supabase/*"]
    for prefix in prefixes:
        behavior = _behavior(contract, prefix)
        assert not _caches(contract, behavior), prefix
        assert behavior["allowed_methods"] == ALL_METHODS, prefix


def test_the_origin_sees_the_viewer_host(contract: dict) -> None:
    """Next.js server actions compare Origin with Host/X-Forwarded-Host, and the
    ingress serves the app domain, so every behavior forwards the viewer Host."""
    for behavior in _behaviors(contract):
        assert _origin_request_policy(contract, behavior)["forwards_viewer_host"] is True


def test_websocket_paths_forward_every_viewer_header(contract: dict) -> None:
    for path in contract["websocket_paths"]:
        behavior = first_matching_behavior(contract, path)
        assert _origin_request_policy(contract, behavior)["headers"] == "all-viewer", path
        assert not _caches(contract, behavior), path


def test_policies_are_the_aws_managed_policies_they_name(contract: dict) -> None:
    referenced = {
        name
        for behavior in _behaviors(contract)
        for name in (behavior["cache_policy"], behavior["origin_request_policy"])
    }
    defined = set(contract["cache_policies"]) | set(contract["origin_request_policies"])

    assert referenced == defined, "every defined policy is used and every used one is defined"
    for name in defined:
        policy = contract["cache_policies"].get(name) or contract["origin_request_policies"][name]
        assert policy["managed_id"] == MANAGED_POLICY_IDS[name], name
    disabled = contract["cache_policies"]["Managed-CachingDisabled"]
    assert (disabled["min_ttl"], disabled["default_ttl"], disabled["max_ttl"]) == (0, 0, 0)
    optimized = contract["cache_policies"]["Managed-CachingOptimized"]
    assert (optimized["min_ttl"], optimized["default_ttl"], optimized["max_ttl"]) == (
        1,
        86400,
        31536000,
    )


def test_origin_settings_are_within_cloudfront_limits(contract: dict) -> None:
    origin = contract["origin"]

    assert 1 <= origin["connection_attempts"] <= 3
    assert 1 <= origin["connection_timeout_seconds"] <= 10
    assert 1 <= origin["response_timeout_seconds"] <= 120
    assert 1 <= origin["keepalive_timeout_seconds"] <= 300
    completion = origin["response_completion_timeout_seconds"]
    assert completion is None or completion >= origin["response_timeout_seconds"]


def test_only_cloudfront_with_the_origin_secret_reaches_the_origin(contract: dict) -> None:
    origin = contract["origin"]
    guard = contract["origin_guard"]
    ingress = origin["access"]["security_group_ingress"]

    assert origin["protocol_policy"] == "http-only"
    assert ingress == {
        "prefix_list": "com.amazonaws.global.cloudfront.origin-facing",
        "protocol": "tcp",
        "port": origin["http_port"],
        "rule_weight": 55,
    }
    assert origin["access"]["ssh"] is False
    headers = origin["custom_headers"]
    assert [item["name"] for item in headers] == [guard["header"]]
    # The value comes from Parameter Store at deploy time, never the repository.
    assert set(headers[0]) == {"name", "value_source", "note"}
    assert re.fullmatch(r"ssm:/motionly/\{environment\}/edge/[a-z-]+", headers[0]["value_source"])
    assert guard["accepted_values"] == ["current", "previous"]
    assert guard["reject_status"] == 403 and guard["strip_before_upstream"] is True
    # A custom origin header must not also be forwarded by name from the viewer.
    for policy in contract["origin_request_policies"].values():
        if isinstance(policy["headers"], list):
            assert guard["header"].lower() not in {h.lower() for h in policy["headers"]}


def test_the_viewer_address_reaches_the_origin(contract: dict) -> None:
    header = contract["client_address"]["header"]

    assert contract["client_address"]["trusted_only_after_origin_guard"] is True
    for behavior in _behaviors(contract):
        if not _caches(contract, behavior):
            assert header in _origin_request_policy(contract, behavior)["cloudfront_headers"]


def test_edge_security_headers_complement_the_app(contract: dict) -> None:
    policies = contract["response_headers_policies"]
    next_config = NEXT_CONFIG.read_text(encoding="utf-8")

    assert {item["response_headers_policy"] for item in _behaviors(contract)} == set(policies)
    policy = policies["motionly-edge-security"]
    hsts = policy["strict_transport_security"]
    assert hsts["override"] is True and hsts["max_age_seconds"] >= 31536000
    # Pages carry a per-request nonce CSP; the edge must not replace it.
    assert policy["content_security_policy"] is None
    # Headers the app also sets: same value, and the app's response wins.
    for name, key in (
        ("X-Frame-Options", "frame_options"),
        ("X-Content-Type-Options", "content_type_options"),
        ("Referrer-Policy", "referrer_policy"),
    ):
        assert f'key: "{name}", value: "{policy[key]["value"]}"' in next_config, name
        assert policy[key]["override"] is False, name
    removed = [name.lower() for name in policy["remove_headers"]]
    assert "server" in removed
    assert not set(removed) & UNREMOVABLE_HEADERS


def test_errors_reach_the_client_uncached(contract: dict) -> None:
    errors = contract["error_caching"]

    assert errors["min_ttl_seconds"] == 0
    assert errors["status_codes"] == CONFIGURABLE_ERROR_CODES
    assert errors["custom_error_pages"] is False


def test_distribution_serves_india_over_modern_http(contract: dict) -> None:
    distribution = contract["distribution"]

    assert distribution["price_class"] in {"PriceClass_200", "PriceClass_All"}
    assert distribution["http_versions"] == "http2and3"
    assert distribution["ipv6"] is True
    assert distribution["viewer_certificate"] == "cloudfront-default"
    # Forced by the default certificate (documented, not a choice).
    assert distribution["viewer_security_policy"] == "TLSv1"


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/api/*", "/api/projects", True),
        ("/api/*", "/api/", True),
        ("/api/*", "/api", False),
        ("/api/*", "/API/projects", False),
        ("/icon.svg", "/icon.svg", True),
        ("/icon.svg", "/icon.svgz", False),
        ("/img/?.png", "/img/a.png", True),
        ("/img/?.png", "/img/ab.png", False),
        ("*", "/anything", True),
    ],
)
def test_path_pattern_semantics(pattern: str, path: str, expected: bool) -> None:
    assert matches(pattern, path) is expected
