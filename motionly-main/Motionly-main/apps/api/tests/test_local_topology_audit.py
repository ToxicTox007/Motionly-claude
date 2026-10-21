"""Regression contracts for the read-only local topology audit plan."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "apps" / "api" / "scripts" / "audit_local_topology.py"
CONTRACT_PATH = ROOT / "infra" / "local-audit" / "service-matrix.json"
COMPOSE_PATH = ROOT / "infra" / "docker-compose.gpu.yml"

spec = importlib.util.spec_from_file_location("audit_local_topology", SCRIPT_PATH)
assert spec and spec.loader
local_topology_audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = local_topology_audit
spec.loader.exec_module(local_topology_audit)


def _write_contract(tmp_path: Path, contract: dict[str, object]) -> Path:
    path = tmp_path / "service-matrix.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _contract() -> dict[str, object]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_plan_only_report_covers_every_service_queue_and_feature_path() -> None:
    report = local_topology_audit.build_audit_report()

    services = report["service_start_order"]
    features = report["feature_test_order"]
    expected_service_order = [
        "volume-permissions",
        "postgres",
        "temporal-postgres",
        "redis",
        "temporal-db-init",
        "temporal-schema",
        "temporal",
        "temporal-namespace",
        "supabase-auth",
        "supabase-rest",
        "supabase-storage",
        "supabase-realtime",
        "supabase-gateway",
        "db-migrate",
        "api",
        "temporal-dispatcher",
        "temporal-worker",
        "workflow-reconciler",
        "web",
        "ingress",
        "cpu-ffmpeg-worker",
        "gpu-inference-worker",
        "matte-worker",
        "gpu-ffmpeg-worker",
        "demucs-worker",
        "provider-worker",
        "planning-worker",
        "remotion-worker",
    ]
    assert [service["name"] for service in services] == expected_service_order
    assert len(services) == len(expected_service_order)
    assert report["audit"]["compose_project_name"] == "motionly-local-audit"
    assert report["audit"]["local_audit"] == {
        "compose_files": [
            "infra/docker-compose.gpu.yml",
            "infra/docker-compose.dev.yml",
            "infra/docker-compose.local-audit.yml",
        ],
        "profile": "local-audit",
        "loopback_ingress_ports": ["127.0.0.1:18080:80", "127.0.0.1:18443:443"],
        "audit_owned_volume_prefix": "motionly-local-audit-",
        "teardown_policy": (
            "Only project-scoped audit-owned resources may be removed; global down -v "
            "and volume pruning are forbidden."
        ),
        "capability_preflight": "apps/api/scripts/audit_local_capabilities.py --plan-only",
        "live_effects_gate": {
            "default": "denied",
            "model_boundary": (
                "ModelGateway.invoke before budget reservation and adapter invocation"
            ),
        },
    }
    assert report["audit"]["side_effects"] == {
        "containers_started": False,
        "provider_calls": False,
        "state_changed": False,
        "files_written": False,
    }

    matrix_queues = {
        queue for service in services for queue in service["worker_queues"]
    }
    feature_queues = {queue for feature in features for queue in feature["queues"]}
    assert matrix_queues == set(report["canonical_worker_queues"])
    assert feature_queues == set(report["canonical_worker_queues"])
    assert report["api_surface"]["path_count"] > 0
    assert report["api_surface"]["operation_count"] > 0


def test_service_start_order_respects_every_declared_dependency() -> None:
    report = local_topology_audit.build_audit_report()
    positions = {
        service["name"]: service["sequence"]
        for service in report["service_start_order"]
    }

    for service in report["service_start_order"]:
        for dependency in service["dependencies"]:
            assert positions[dependency["service"]] < positions[service["name"]]


def test_contract_fails_when_compose_adds_unclassified_service(tmp_path: Path) -> None:
    mutated_compose = tmp_path / "docker-compose.gpu.yml"
    source = COMPOSE_PATH.read_text(encoding="utf-8")
    source = source.replace(
        "\nvolumes:\n",
        "\n  audit-fixture:\n    image: alpine:3.21.3\n    networks: [app]\n\nvolumes:\n",
        1,
    )
    mutated_compose.write_text(source, encoding="utf-8")

    with pytest.raises(
        local_topology_audit.AuditContractError,
        match="unclassified Compose service",
    ):
        local_topology_audit.build_audit_report(compose_path=mutated_compose)


def test_contract_fails_when_canonical_worker_queue_is_absent_from_matrix(
    tmp_path: Path,
) -> None:
    contract = _contract()
    services = contract["services"]
    assert isinstance(services, list)
    cpu_worker = next(service for service in services if service["name"] == "cpu-ffmpeg-worker")
    cpu_worker["worker_queues"].remove("render")

    with pytest.raises(
        local_topology_audit.AuditContractError,
        match=r"worker queue\(s\) absent from service matrix",
    ):
        local_topology_audit.build_audit_report(contract_path=_write_contract(tmp_path, contract))


def test_contract_fails_when_required_verification_has_no_owner(tmp_path: Path) -> None:
    contract = _contract()
    feature_tests = contract["feature_tests"]
    assert isinstance(feature_tests, list)
    feature_tests[0]["verification"]["owner"] = ""

    with pytest.raises(
        local_topology_audit.AuditContractError,
        match="feature test api-openapi-surface verification lacks owner",
    ):
        local_topology_audit.build_audit_report(contract_path=_write_contract(tmp_path, contract))


def test_plan_only_mode_is_required_and_json_report_is_machine_readable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as missing_plan_only:
        local_topology_audit.main([])
    assert missing_plan_only.value.code == 2
    assert local_topology_audit.main(["--plan-only", "--format", "json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["audit"]["mode"] == "plan-only"
    assert [service["name"] for service in report["service_start_order"]][:4] == [
        "volume-permissions",
        "postgres",
        "temporal-postgres",
        "redis",
    ]
