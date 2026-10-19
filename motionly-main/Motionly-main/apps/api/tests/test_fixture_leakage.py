"""Fixture-leakage gate (Task 7).

fixtures/registry.json lists every stand-in for a real capability. This gate
fails when a stand-in exists in production code but is not registered, when a
registered symbol is gone, when production accepts a setting that turns a
fixture on, when a production deployment input enables one, or when test
infrastructure is wired into production.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from app.core.production import validate_production_settings
from tests.test_service_hardening import _production_settings

ROOT = Path(__file__).resolve().parents[3]
REGISTRY = json.loads((ROOT / "fixtures" / "registry.json").read_text(encoding="utf-8"))
ENTRIES = REGISTRY["entries"]
FIXTURES = [entry for entry in ENTRIES if entry["kind"] == "fixture"]

PRODUCTION_CODE = [
    ROOT / "apps" / "api" / "app",
    ROOT / "apps" / "worker" / "src",
    ROOT / "apps" / "renderer" / "src",
    ROOT / "apps" / "web" / "app",
    ROOT / "apps" / "web" / "components",
    ROOT / "apps" / "web" / "lib",
]
# Names that mark a stand-in for a real capability.
STAND_IN = re.compile(
    r"^(_?)(Fixture[A-Z]\w*|Fake[A-Z]\w*|Stub[A-Z]\w*|Mock[A-Z]\w*|Dummy[A-Z]\w*"
    r"|Hash[A-Z]\w*Provider|\w*simulated\w*|fixture[A-Z]\w*)$"
)
# Settings that turn each production-rejected fixture on, as Settings fields.
FIELDS = {
    "VISUAL_PROVIDER": "visual_intelligence_provider",
    "TRANSCRIPTION_PROVIDER": "transcription_provider",
    "EDIT_PLANNER_PROVIDER": "edit_planner_provider",
    "STEM_SEPARATION_PROVIDER": "stem_separation_provider",
    "ALLOW_FIXTURE_PROVIDERS": "allow_fixture_providers",
    "ML_EXECUTION_MODE": "ml_execution_mode",
    "RUNWAY_API_KEY": "runway_api_key",
    "RENDER_EXECUTE_FFMPEG": "render_execute_ffmpeg",
}


def _defined_names(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return {
            node.name
            for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
    return set(
        re.findall(
            r"(?:^|\n)\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_]\w*)", text
        )
    )


def _production_sources():
    for directory in PRODUCTION_CODE:
        for path in directory.rglob("*"):
            if path.suffix not in {".py", ".mjs", ".ts", ".tsx"}:
                continue
            if re.search(r"(\.test\.|/tests?/|/__tests__/|\.d\.mts$)", str(path)):
                continue
            yield path


def test_registry_entries_are_complete_and_unique() -> None:
    assert REGISTRY["schema_version"] == 1
    ids = [entry["id"] for entry in ENTRIES]
    assert len(ids) == len(set(ids))
    for entry in ENTRIES:
        assert entry["kind"] in {"fixture", "test_infrastructure", "demo"}, entry
        for field in ("id", "label", "symbol", "path", "stands_in_for", "identified_by"):
            assert str(entry.get(field, "")).strip(), (entry["id"], field)
        expected = {
            "fixture": {"rejected", "unreachable"},
            "test_infrastructure": {"never_deployed"},
        }
        assert entry["production"] in expected.get(entry["kind"], {"demo_mode_only"}), entry
        if entry["kind"] == "fixture":
            assert entry["enabled_by"], entry


@pytest.mark.parametrize("entry", ENTRIES, ids=[entry["id"] for entry in ENTRIES])
def test_every_registered_stand_in_exists_where_the_registry_says(entry: dict) -> None:
    path = ROOT / entry["path"]
    assert path.is_file(), entry["path"]
    symbol = entry["symbol"]
    if re.fullmatch(r"[A-Za-z_]\w*", symbol):
        assert symbol in _defined_names(path), f"{symbol} is not defined in {entry['path']}"
    else:
        assert f'"{symbol}"' in path.read_text(encoding="utf-8"), symbol


def test_every_stand_in_in_production_code_is_registered() -> None:
    registered = {(entry["path"], entry["symbol"]) for entry in ENTRIES}
    unregistered = sorted(
        f"{path.relative_to(ROOT)}: {name}"
        for path in _production_sources()
        for name in _defined_names(path)
        if STAND_IN.match(name) and (str(path.relative_to(ROOT)), name) not in registered
    )
    assert unregistered == [], "register these in fixtures/registry.json"


@pytest.mark.parametrize(
    ("entry", "variable", "value"),
    [
        (entry, variable, value)
        for entry in FIXTURES
        if entry["production"] == "rejected"
        for variable, value in entry["enabled_by"].items()
    ],
    ids=lambda item: item["id"] if isinstance(item, dict) else str(item),
)
def test_production_refuses_every_setting_that_turns_a_fixture_on(
    entry: dict, variable: str, value: str
) -> None:
    field = FIELDS[variable]
    setting: object = {"true": True, "false": False, "": None}.get(value, value)
    for role in ("api", "worker"):
        with pytest.raises(RuntimeError):
            validate_production_settings(
                _production_settings(process_role=role, **{field: setting})
            )


def test_production_never_registers_the_hash_embedding_fallback(monkeypatch) -> None:
    from app.services.model_domain_adapters import register_clip_routes
    from app.services.model_gateway import ModelGateway

    gateway = ModelGateway(adapters={}, routes=[])
    register_clip_routes(
        gateway,
        primary_provider=object(),
        primary_revision="clip-primary-2026-07-14",
        fallback_provider=None,
    )
    assert [route.route_id for route in gateway.routes] == ["local-clip-primary"]

    # main.py passes no fallback provider in production.
    source = (ROOT / "apps" / "api" / "app" / "main.py").read_text(encoding="utf-8")
    start = source.index("register_clip_routes(\n")
    call = source[start : source.index("\n        )\n", start)]
    assert "HashClipEmbeddingProvider(" in call
    assert 'if settings.app_env != "production"' in call
    assert "else None" in call


def _compose(path: Path) -> dict:
    class Loader(yaml.SafeLoader):
        pass

    def construct(loader, _suffix, node):
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        return loader.construct_scalar(node)

    Loader.add_multi_constructor("!", construct)
    return yaml.load(path.read_text(encoding="utf-8"), Loader=Loader)


def _enabling(variable: str, value: object) -> bool:
    text = str(value).strip().strip('"').lower()
    return {
        "VISUAL_PROVIDER": text == "fixture",
        "TRANSCRIPTION_PROVIDER": text == "fixture",
        "EDIT_PLANNER_PROVIDER": text == "fixture",
        "STEM_SEPARATION_PROVIDER": text == "fixture",
        "ALLOW_FIXTURE_PROVIDERS": text == "true",
        "ML_EXECUTION_MODE": text == "test_fixture",
        "RENDER_EXECUTE_FFMPEG": text == "false",
        "MUSIC_PROVIDER": text == "fixture",
        "STOCK_VIDEO_PROVIDER": text == "fixture",
    }.get(variable, False)


def test_production_deployment_inputs_enable_no_fixture() -> None:
    offenders = []
    compose = _compose(ROOT / "infra" / "docker-compose.gpu.yml")
    # Shared environment blocks (x-*-environment) are merged into services.
    blocks: dict = {}
    for key, value in compose.items():
        if key.startswith("x-") and isinstance(value, dict):
            blocks.update(value)
    for name, service in compose["services"].items():
        environment = service.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(item.split("=", 1) for item in environment if "=" in item)
        for variable, value in {**blocks, **environment}.items():
            if _enabling(variable, value):
                offenders.append(f"docker-compose.gpu.yml {name}: {variable}={value}")
    for template in ("prod.env.example", "infra/production-compose.env.example"):
        for line in (ROOT / template).read_text(encoding="utf-8").splitlines():
            variable, _, value = line.partition("=")
            if _enabling(variable.strip(), value):
                offenders.append(f"{template}: {line}")
    assert offenders == []


def test_test_infrastructure_is_wired_only_into_the_cpu_stage() -> None:
    production = (ROOT / "infra" / "docker-compose.gpu.yml").read_text(encoding="utf-8")
    assert "cdn-sim" not in production
    assert "edge_tls" not in production and "edge_ca_cert" not in production
    for dockerfile in ROOT.glob("**/Dockerfile*"):
        if "node_modules" in dockerfile.parts:
            continue
        text = dockerfile.read_text(encoding="utf-8")
        assert "scripts/cdn-sim" not in text and "COPY scripts" not in text, dockerfile
    overlay = (ROOT / "infra" / "docker-compose.cpu-stage.yml").read_text(encoding="utf-8")
    assert "cdn-sim" in overlay
