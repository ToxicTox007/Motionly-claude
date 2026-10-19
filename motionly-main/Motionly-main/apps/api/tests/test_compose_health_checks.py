"""Every long-running service reports health, so `compose up --wait` at release
time fails on a service that never becomes healthy or crash-loops, instead of
declaring the release live (Task 9).
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = [
    ROOT / "infra" / "docker-compose.gpu.yml",
    ROOT / "infra" / "docker-compose.cpu-stage.yml",
]
# Services whose image contains nothing that can run a probe, with the reason
# and what covers them instead.
NO_PROBE = {
    "supabase-rest": "postgrest/postgrest is a single static binary with no shell or HTTP "
    "client; the stack checker and the browser suite exercise REST through the gateway.",
}
WORKER_LIVENESS = "/tmp/motionly-worker-alive"
ORCHESTRATION_LIVENESS = "/tmp/motionly-orchestration-alive"


class _Loader(yaml.SafeLoader):
    pass


def _construct(loader, _suffix, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_Loader.add_multi_constructor("!", _construct)


def _services(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)["services"]


def _long_running(path: Path) -> dict:
    return {
        name: service
        for name, service in _services(path).items()
        if str(service.get("restart", "")).strip('"') != "no"
    }


def test_every_long_running_production_service_has_a_health_check() -> None:
    missing = sorted(
        name
        for name, service in _long_running(COMPOSE_FILES[0]).items()
        if "healthcheck" not in service and name not in NO_PROBE
    )
    assert missing == []
    for name in NO_PROBE:
        assert name in _services(COMPOSE_FILES[0])


def test_services_added_by_the_cpu_stage_overlay_have_health_checks() -> None:
    base = _services(COMPOSE_FILES[0])
    added = {
        name: service
        for name, service in _long_running(COMPOSE_FILES[1]).items()
        if name not in base
    }
    assert added, "the overlay adds the CPU-stage worker services"

    def checked(service: dict) -> bool:
        # `extends` inherits the base service's health check.
        parent = (service.get("extends") or {}).get("service")
        return "healthcheck" in service or (parent is not None and "healthcheck" in base[parent])

    assert sorted(name for name, service in added.items() if not checked(service)) == []


def _test(service: dict) -> str:
    return " ".join(service["healthcheck"]["test"])


def test_workers_and_orchestration_roles_report_liveness_files() -> None:
    services = _services(COMPOSE_FILES[0])
    for name in (
        "cpu-ffmpeg-worker",
        "gpu-inference-worker",
        "matte-worker",
        "gpu-ffmpeg-worker",
        "demucs-worker",
        "provider-worker",
        "planning-worker",
        "remotion-worker",
    ):
        assert WORKER_LIVENESS in _test(services[name]), name
    for name in ("temporal-dispatcher", "temporal-worker", "workflow-reconciler"):
        assert ORCHESTRATION_LIVENESS in _test(services[name]), name
    assert "/health/live" in _test(services["api"])
    assert "/app-api/health" in _test(services["web"])
    assert "2019" in _test(services["ingress"])
