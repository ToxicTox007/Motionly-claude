#!/usr/bin/env python3
"""Validate and render Motionly's side-effect-free local topology audit plan.

The command intentionally reads repository artifacts only. It does not import the
application, invoke Docker, start containers, contact providers, or write evidence.
A later runtime stage must use the isolated Compose project declared in
``infra/local-audit/service-matrix.json`` and satisfy its external-action preflight.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTRACT_PATH = ROOT / "infra" / "local-audit" / "service-matrix.json"

SERVICE_HEADER_PATTERN = re.compile(r"^  (?P<name>[a-z0-9][a-z0-9-]*):\n", re.MULTILINE)
QUEUE_LIST_PATTERN = re.compile(
    r"export const allQueueNames\s*=\s*\[(?P<values>.*?)\];", re.DOTALL
)
QUOTED_VALUE_PATTERN = re.compile(r'["\'](?P<value>[^"\']+)["\']')


class AuditContractError(RuntimeError):
    """Raised when the static local-audit contract no longer matches its sources."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditContractError(f"cannot read JSON source {path}: {error}") from error
    if not isinstance(content, dict):
        raise AuditContractError(f"JSON source must be an object: {path}")
    return content


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _field_block(service_block: str, field: str) -> tuple[str | None, list[str]]:
    """Return an inline Compose value and its indented child lines, if present."""

    lines = service_block.splitlines()
    prefix = f"    {field}:"
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        inline_value = line[len(prefix) :].strip()
        if inline_value:
            return inline_value, []
        child_lines: list[str] = []
        for child in lines[index + 1 :]:
            is_next_field = (
                child.startswith("    ")
                and not child.startswith("      ")
                and re.match(r"^    [a-z][a-z0-9_-]*:", child)
            )
            if is_next_field:
                break
            child_lines.append(child)
        return None, child_lines
    return None, []


def _parse_dependency_mapping(lines: list[str]) -> list[dict[str, str]]:
    content = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not content:
        return []
    base_indent = min(_leading_spaces(line) for line in content)
    dependencies: list[dict[str, str]] = []
    for index, line in enumerate(content):
        match = re.match(
            rf"^ {{{base_indent}}}(?P<service>[a-z0-9][a-z0-9-]*):(?:\s*(?P<rest>.*))?$",
            line,
        )
        if not match:
            continue
        service = match.group("service")
        remainder = match.group("rest") or ""
        condition_match = re.search(r"condition:\s*([a-z_]+)", remainder)
        if condition_match is None:
            for child in content[index + 1 :]:
                if _leading_spaces(child) <= base_indent:
                    break
                condition_match = re.search(r"condition:\s*([a-z_]+)", child)
                if condition_match is not None:
                    break
        condition = condition_match.group(1) if condition_match else "service_started"
        dependencies.append({"service": service, "condition": condition})
    return dependencies


def _parse_dependency_anchors(source: str) -> dict[str, list[dict[str, str]]]:
    """Extract named Compose anchors before services without parsing all YAML."""

    anchors: dict[str, list[dict[str, str]]] = {}
    anchor_pattern = re.compile(
        r"^x-[a-z0-9-]+:\s*&(?P<name>[a-z0-9-]+)(?:\s+#.*)?$",
        re.MULTILINE,
    )
    section_pattern = re.compile(r"^(?:x-[a-z0-9-]+:|services:)", re.MULTILINE)
    for match in anchor_pattern.finditer(source):
        start = match.end()
        next_section = section_pattern.search(source, start)
        end = next_section.start() if next_section else len(source)
        anchors[match.group("name")] = _parse_dependency_mapping(
            source[start:end].splitlines()
        )
    return anchors


def _parse_dependencies(
    service_block: str, dependency_anchors: dict[str, list[dict[str, str]]]
) -> list[dict[str, str]]:
    inline_value, child_lines = _field_block(service_block, "depends_on")
    if inline_value is None:
        return _parse_dependency_mapping(child_lines)
    if inline_value.startswith("*"):
        alias = inline_value[1:].strip()
        if alias not in dependency_anchors:
            raise AuditContractError(f"unknown Compose depends_on anchor: {alias}")
        return dependency_anchors[alias]
    if inline_value.startswith("[") and inline_value.endswith("]"):
        names = [name.strip().strip("\"'") for name in inline_value[1:-1].split(",")]
        return [
            {"service": name, "condition": "service_started"}
            for name in names
            if name
        ]
    if inline_value.startswith("{") and inline_value.endswith("}"):
        dependencies = [
            {"service": match.group("service"), "condition": match.group("condition")}
            for match in re.finditer(
                r"(?P<service>[a-z0-9][a-z0-9-]*):\s*\{\s*condition:\s*"
                r"(?P<condition>[a-z_]+)\s*\}",
                inline_value,
            )
        ]
        if dependencies:
            return dependencies
    raise AuditContractError(f"unsupported depends_on declaration: {inline_value}")


def _parse_inline_strings(value: str | None, child_lines: list[str]) -> list[str]:
    if value is not None:
        if value.startswith("[") and value.endswith("]"):
            return [match.group("value") for match in QUOTED_VALUE_PATTERN.finditer(value)]
        return [value.strip("\"'")]
    values: list[str] = []
    for line in child_lines:
        match = re.match(r"^\s*-\s*[\"']?(?P<value>[^\"'#]+)[\"']?\s*$", line)
        if match:
            values.append(match.group("value").strip())
    return values


def _parse_worker_queues(service_block: str) -> list[str]:
    match = re.search(
        r"^\s+WORKER_QUEUES:\s*(?P<queues>[^\n#]+)",
        service_block,
        re.MULTILINE,
    )
    if match is None:
        return []
    return [queue.strip() for queue in match.group("queues").split(",") if queue.strip()]


def parse_compose_services(compose_path: Path) -> dict[str, dict[str, Any]]:
    """Extract the audited Compose subset without a YAML dependency or Docker CLI."""

    try:
        source = compose_path.read_text(encoding="utf-8")
        services_start = source.index("services:\n") + len("services:\n")
        services_end = source.index("\nvolumes:\n", services_start)
    except (OSError, ValueError) as error:
        message = f"cannot parse Compose services from {compose_path}: {error}"
        raise AuditContractError(message) from error

    service_section = source[services_start:services_end]
    service_headers = list(SERVICE_HEADER_PATTERN.finditer(service_section))
    if not service_headers:
        raise AuditContractError(f"no Compose services found in {compose_path}")

    dependency_anchors = _parse_dependency_anchors(source)
    services: dict[str, dict[str, Any]] = {}
    for index, header in enumerate(service_headers):
        name = header.group("name")
        block_start = header.end()
        if index + 1 < len(service_headers):
            block_end = service_headers[index + 1].start()
        else:
            block_end = len(service_section)
        block = service_section[block_start:block_end]
        ports_value, ports_children = _field_block(block, "ports")
        healthcheck_value, healthcheck_children = _field_block(block, "healthcheck")
        services[name] = {
            "name": name,
            "dependencies": _parse_dependencies(block, dependency_anchors),
            "expected_ports": _parse_inline_strings(ports_value, ports_children),
            "healthcheck": (
                "required"
                if healthcheck_value is not None or healthcheck_children
                else "none"
            ),
            "worker_queues": _parse_worker_queues(block),
        }
    return services


def parse_canonical_worker_queues(queue_registry_path: Path) -> list[str]:
    try:
        source = queue_registry_path.read_text(encoding="utf-8")
    except OSError as error:
        message = f"cannot read worker queue registry {queue_registry_path}: {error}"
        raise AuditContractError(message) from error
    match = QUEUE_LIST_PATTERN.search(source)
    if match is None:
        raise AuditContractError(f"cannot find allQueueNames in {queue_registry_path}")
    queues = [
        item.group("value")
        for item in QUOTED_VALUE_PATTERN.finditer(match.group("values"))
    ]
    if not queues:
        raise AuditContractError(f"worker queue registry is empty: {queue_registry_path}")
    return queues


def _validate_marked_source(entry: dict[str, Any], label: str, errors: list[str]) -> None:
    path_value = entry.get("path")
    owner = entry.get("owner")
    if not isinstance(path_value, str) or not path_value:
        errors.append(f"{label} must declare a source path")
        return
    if not isinstance(owner, str) or not owner.strip():
        errors.append(f"{label} lacks an owner")
    path = ROOT / path_value
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"{label} cannot be read: {error}")
        return
    for marker in entry.get("required_markers", []):
        if marker not in source:
            errors.append(f"{label} is missing required marker: {marker}")


def _validate_verification(entity: dict[str, Any], label: str, errors: list[str]) -> None:
    verification = entity.get("verification")
    if not isinstance(verification, dict):
        errors.append(f"{label} lacks a verification step")
        return
    for field in ("owner", "smoke_action", "log_pattern", "cleanup_responsibility"):
        value = verification.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label} verification lacks {field}")


def _validate_external_side_effects(
    entity: dict[str, Any], label: str, errors: list[str]
) -> None:
    side_effects = entity.get("external_side_effects")
    if not isinstance(side_effects, dict):
        errors.append(f"{label} lacks external-side-effect classification")
        return
    if not isinstance(side_effects.get("can_invoke_external_systems"), bool):
        errors.append(f"{label} must declare whether it can invoke external systems")
    if not isinstance(side_effects.get("systems"), list):
        errors.append(f"{label} must list external systems, even when empty")
    preflight = side_effects.get("preflight")
    if not isinstance(preflight, str) or not preflight.strip():
        errors.append(f"{label} lacks an external-action preflight classification")


def _service_start_order(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the declared phase order after validation proves it is dependency-safe."""

    return sorted(services, key=lambda service: (service["order"], service["name"]))


def _openapi_summary(openapi_path: Path) -> dict[str, Any]:
    document = _read_json(openapi_path)
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise AuditContractError(f"OpenAPI document has no paths: {openapi_path}")
    operations = [
        operation
        for path_item in paths.values()
        if isinstance(path_item, dict)
        for method, operation in path_item.items()
        if method.lower() in {"delete", "get", "head", "options", "patch", "post", "put"}
        and isinstance(operation, dict)
    ]
    tags = sorted(
        {
            tag
            for operation in operations
            for tag in operation.get("tags", [])
            if isinstance(tag, str)
        }
    )
    return {"path_count": len(paths), "operation_count": len(operations), "tags": tags}


def validate_contract(
    contract: dict[str, Any],
    compose_services: dict[str, dict[str, Any]],
    canonical_queues: list[str],
) -> list[str]:
    """Return all contract violations without a container or provider call."""

    errors: list[str] = []
    if contract.get("schema_version") != 1:
        errors.append("service matrix must declare schema_version 1")

    audit = contract.get("audit")
    if not isinstance(audit, dict):
        errors.append("service matrix lacks audit metadata")
    else:
        for field in (
            "compose_file",
            "compose_project_name",
            "compose_project_name_environment_variable",
            "future_runtime_command",
            "namespace_scope",
            "teardown_owner",
        ):
            value = audit.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"audit metadata lacks {field}")
        preflight = audit.get("runtime_preflight")
        if not isinstance(preflight, dict) or preflight.get("failure_mode") != "blocked":
            errors.append("runtime preflight must fail closed")
        elif not preflight.get("required_before_external_actions"):
            errors.append("runtime preflight must name external-action prerequisites")
        local_audit = audit.get("local_audit")
        if not isinstance(local_audit, dict):
            errors.append("service matrix lacks local-audit runtime metadata")
        else:
            compose_files = local_audit.get("compose_files")
            if compose_files != [
                "infra/docker-compose.gpu.yml",
                "infra/docker-compose.dev.yml",
                "infra/docker-compose.local-audit.yml",
            ]:
                errors.append("local-audit Compose merge order is invalid")
            if local_audit.get("profile") != "local-audit":
                errors.append("local-audit profile must be local-audit")
            ports = local_audit.get("loopback_ingress_ports")
            if ports != ["127.0.0.1:18080:80", "127.0.0.1:18443:443"]:
                errors.append("local-audit ingress must be loopback-only")
            if local_audit.get("audit_owned_volume_prefix") != "motionly-local-audit-":
                errors.append("local-audit volume prefix is invalid")
            teardown_policy = local_audit.get("teardown_policy")
            if not isinstance(teardown_policy, str) or "global down -v" not in teardown_policy:
                errors.append("local-audit teardown policy must forbid global down -v")
            if local_audit.get("capability_preflight") != (
                "apps/api/scripts/audit_local_capabilities.py --plan-only"
            ):
                errors.append("local-audit capability preflight is invalid")
            live_effects_gate = local_audit.get("live_effects_gate")
            if (
                not isinstance(live_effects_gate, dict)
                or live_effects_gate.get("default") != "denied"
            ):
                errors.append("local-audit live effects must default to denied")

    evidence_sources = contract.get("evidence_sources")
    if not isinstance(evidence_sources, dict):
        errors.append("service matrix lacks evidence sources")
    else:
        for source_name in ("queue_registry", "api_application"):
            entry = evidence_sources.get(source_name)
            if not isinstance(entry, dict):
                errors.append(f"service matrix lacks {source_name} evidence")
            else:
                _validate_marked_source(entry, source_name, errors)
        openapi_entry = evidence_sources.get("openapi")
        if not isinstance(openapi_entry, dict):
            errors.append("service matrix lacks OpenAPI evidence")
        else:
            _validate_marked_source(openapi_entry, "openapi", errors)
        ci_workflows = evidence_sources.get("ci_workflows")
        if not isinstance(ci_workflows, list) or not ci_workflows:
            errors.append("service matrix lacks CI workflow evidence")
        else:
            for workflow in ci_workflows:
                if isinstance(workflow, dict):
                    _validate_marked_source(workflow, "CI workflow", errors)
                else:
                    errors.append("CI workflow evidence must be an object")

    services = contract.get("services")
    if not isinstance(services, list) or not services:
        return [*errors, "service matrix must contain service classifications"]
    service_matrix: dict[str, dict[str, Any]] = {}
    for service in services:
        if not isinstance(service, dict):
            errors.append("service matrix entries must be objects")
            continue
        name = service.get("name")
        if not isinstance(name, str) or not name:
            errors.append("service matrix entry lacks name")
            continue
        if name in service_matrix:
            errors.append(f"duplicate service classification: {name}")
            continue
        service_matrix[name] = service
        if not isinstance(service.get("order"), int):
            errors.append(f"service {name} lacks numeric order")
        phase = service.get("phase")
        if not isinstance(phase, str) or not phase.strip():
            errors.append(f"service {name} lacks phase")
        predicate = service.get("startup_predicate")
        if not isinstance(predicate, str) or not predicate.strip():
            errors.append(f"service {name} lacks startup predicate")
        ownership = service.get("feature_ownership")
        if not isinstance(ownership, list) or not ownership:
            errors.append(f"service {name} lacks feature ownership")
        _validate_verification(service, f"service {name}", errors)
        _validate_external_side_effects(service, f"service {name}", errors)

    unclassified = sorted(set(compose_services) - set(service_matrix))
    if unclassified:
        errors.append("unclassified Compose service(s): " + ", ".join(unclassified))
    stale = sorted(set(service_matrix) - set(compose_services))
    if stale:
        errors.append("service matrix contains non-Compose service(s): " + ", ".join(stale))

    for name in sorted(set(compose_services) & set(service_matrix)):
        declared = service_matrix[name]
        actual = compose_services[name]
        for field in ("dependencies", "expected_ports", "healthcheck", "worker_queues"):
            if declared.get(field) != actual[field]:
                errors.append(
                    f"service {name} {field} drift: expected {declared.get(field)!r}, "
                    f"Compose declares {actual[field]!r}"
                )
        for dependency in declared.get("dependencies", []):
            dependency_name = dependency.get("service") if isinstance(dependency, dict) else None
            if dependency_name not in service_matrix:
                errors.append(
                    f"service {name} depends on unclassified service {dependency_name!r}"
                )
            elif isinstance(declared.get("order"), int) and isinstance(
                service_matrix[dependency_name].get("order"), int
            ) and declared["order"] <= service_matrix[dependency_name]["order"]:
                errors.append(f"service {name} is ordered before dependency {dependency_name}")

    matrix_queue_counts = Counter(
        queue
        for service in services
        if isinstance(service, dict)
        for queue in service.get("worker_queues", [])
        if isinstance(queue, str)
    )
    compose_queue_counts = Counter(
        queue for service in compose_services.values() for queue in service["worker_queues"]
    )
    canonical_queue_set = set(canonical_queues)
    missing_matrix_queues = sorted(canonical_queue_set - set(matrix_queue_counts))
    if missing_matrix_queues:
        errors.append(
            "worker queue(s) absent from service matrix: "
            + ", ".join(missing_matrix_queues)
        )
    unexpected_matrix_queues = sorted(set(matrix_queue_counts) - canonical_queue_set)
    if unexpected_matrix_queues:
        errors.append(
            "service matrix has unknown worker queue(s): "
            + ", ".join(unexpected_matrix_queues)
        )
    for queue, count in sorted(matrix_queue_counts.items()):
        if count != 1:
            errors.append(f"service matrix assigns worker queue {queue} {count} times")
    if set(compose_queue_counts) != canonical_queue_set:
        errors.append(
            "Compose worker queues do not equal canonical registry: "
            f"missing={sorted(canonical_queue_set - set(compose_queue_counts))}, "
            f"unexpected={sorted(set(compose_queue_counts) - canonical_queue_set)}"
        )
    for queue, count in sorted(compose_queue_counts.items()):
        if count != 1:
            errors.append(f"Compose assigns worker queue {queue} {count} times")

    feature_tests = contract.get("feature_tests")
    if not isinstance(feature_tests, list) or not feature_tests:
        return [*errors, "service matrix must contain feature tests"]
    feature_ids: set[str] = set()
    feature_queue_counts: Counter[str] = Counter()
    for feature in feature_tests:
        if not isinstance(feature, dict):
            errors.append("feature test entries must be objects")
            continue
        feature_id = feature.get("id")
        if not isinstance(feature_id, str) or not feature_id:
            errors.append("feature test lacks id")
            label = "feature test <unnamed>"
        elif feature_id in feature_ids:
            errors.append(f"duplicate feature test id: {feature_id}")
            label = f"feature test {feature_id}"
        else:
            feature_ids.add(feature_id)
            label = f"feature test {feature_id}"
        if not isinstance(feature.get("order"), int):
            errors.append(f"{label} lacks numeric order")
        requires_services = feature.get("requires_services")
        if not isinstance(requires_services, list) or not requires_services:
            errors.append(f"{label} must name required services")
        else:
            for service_name in requires_services:
                if service_name not in service_matrix:
                    errors.append(f"{label} requires unclassified service {service_name!r}")
        queues = feature.get("queues")
        if not isinstance(queues, list):
            errors.append(f"{label} must declare queues, including an empty list")
        else:
            for queue in queues:
                if queue not in canonical_queue_set:
                    errors.append(f"{label} names unknown worker queue {queue!r}")
                elif not isinstance(queue, str):
                    errors.append(f"{label} queue must be a string")
                else:
                    feature_queue_counts[queue] += 1
        _validate_verification(feature, label, errors)
        _validate_external_side_effects(feature, label, errors)

    missing_feature_queues = sorted(canonical_queue_set - set(feature_queue_counts))
    if missing_feature_queues:
        errors.append(
            "worker queue(s) absent from feature tests: "
            + ", ".join(missing_feature_queues)
        )
    for queue, count in sorted(feature_queue_counts.items()):
        if count != 1:
            errors.append(f"feature tests cover worker queue {queue} {count} times")
    return errors


def build_audit_report(
    contract_path: Path = DEFAULT_CONTRACT_PATH,
    compose_path: Path | None = None,
) -> dict[str, Any]:
    """Build a deterministic audit report from repository files without side effects."""

    contract = _read_json(contract_path)
    audit = contract.get("audit")
    if not isinstance(audit, dict) or not isinstance(audit.get("compose_file"), str):
        raise AuditContractError("service matrix lacks audit.compose_file")
    evidence = contract.get("evidence_sources")
    queue_registry = evidence.get("queue_registry") if isinstance(evidence, dict) else None
    if not isinstance(queue_registry, dict):
        raise AuditContractError("service matrix lacks queue registry evidence")
    queue_path_value = queue_registry.get("path")
    if not isinstance(queue_path_value, str):
        raise AuditContractError("queue registry evidence lacks path")
    openapi_entry = evidence.get("openapi")
    if not isinstance(openapi_entry, dict) or not isinstance(openapi_entry.get("path"), str):
        raise AuditContractError("service matrix lacks OpenAPI evidence path")

    resolved_compose_path = compose_path or ROOT / audit["compose_file"]
    compose_services = parse_compose_services(resolved_compose_path)
    canonical_queues = parse_canonical_worker_queues(ROOT / queue_path_value)
    errors = validate_contract(contract, compose_services, canonical_queues)
    if errors:
        raise AuditContractError("local audit contract violations:\n- " + "\n- ".join(errors))

    services = contract["services"]
    ordered_services = _service_start_order(services)
    by_name = {service["name"]: service for service in services}
    ordered_features = sorted(
        contract["feature_tests"],
        key=lambda feature: (
            max(by_name[service]["order"] for service in feature["requires_services"]),
            feature["order"],
            feature["id"],
        ),
    )
    ci_evidence = [
        {
            "path": workflow["path"],
            "owner": workflow["owner"],
            "markers_verified": workflow.get("required_markers", []),
        }
        for workflow in evidence["ci_workflows"]
    ]
    return {
        "audit": {
            "mode": audit["mode"],
            "compose_project_name": audit["compose_project_name"],
            "compose_project_name_environment_variable": audit[
                "compose_project_name_environment_variable"
            ],
            "future_runtime_command": audit["future_runtime_command"],
            "namespace_scope": audit["namespace_scope"],
            "teardown_owner": audit["teardown_owner"],
            "runtime_preflight": audit["runtime_preflight"],
            "local_audit": audit["local_audit"],
            "side_effects": {
                "containers_started": False,
                "provider_calls": False,
                "state_changed": False,
                "files_written": False,
            },
        },
        "sources": {
            "compose": str(resolved_compose_path.relative_to(ROOT)),
            "queue_registry": queue_path_value,
            "openapi": openapi_entry["path"],
            "ci_workflows": ci_evidence,
        },
        "api_surface": _openapi_summary(ROOT / openapi_entry["path"]),
        "canonical_worker_queues": canonical_queues,
        "service_start_order": [
            {
                "sequence": index,
                "name": service["name"],
                "phase": service["phase"],
                "dependencies": service["dependencies"],
                "expected_ports": service["expected_ports"],
                "healthcheck": service["healthcheck"],
                "worker_queues": service["worker_queues"],
                "feature_ownership": service["feature_ownership"],
                "startup_predicate": service["startup_predicate"],
                "verification": service["verification"],
                "external_side_effects": service["external_side_effects"],
            }
            for index, service in enumerate(ordered_services, start=1)
        ],
        "feature_test_order": [
            {
                "sequence": index,
                "id": feature["id"],
                "requires_services": feature["requires_services"],
                "queues": feature["queues"],
                "verification": feature["verification"],
                "external_side_effects": feature["external_side_effects"],
            }
            for index, feature in enumerate(ordered_features, start=1)
        ],
    }


def render_text_plan(report: dict[str, Any]) -> str:
    audit = report["audit"]
    lines = [
        "Motionly local audit plan (read-only)",
        f"Compose project namespace: {audit['compose_project_name']}",
        f"Future runtime command: {audit['future_runtime_command']}",
        "No containers, provider calls, state changes, or files were created by this command.",
        "",
        "Service startup order:",
    ]
    for service in report["service_start_order"]:
        dependencies = ", ".join(
            f"{dependency['service']} ({dependency['condition']})"
            for dependency in service["dependencies"]
        ) or "none"
        queues = ", ".join(service["worker_queues"]) or "none"
        ports = ", ".join(service["expected_ports"]) or "private only"
        verification = service["verification"]
        side_effects = service["external_side_effects"]
        external = "yes" if side_effects["can_invoke_external_systems"] else "no"
        dependency_line = (
            f"    depends on: {dependencies}; ports: {ports}; "
            f"healthcheck: {service['healthcheck']}; queues: {queues}"
        )
        verification_line = (
            f"    verify ({verification['owner']}): {verification['smoke_action']}"
        )
        external_line = f"    external systems: {external} ({side_effects['preflight']})"
        lines.extend(
            [
                f"{service['sequence']:02d}. {service['name']} [{service['phase']}]",
                dependency_line,
                f"    predicate: {service['startup_predicate']}",
                verification_line,
                external_line,
            ]
        )
    lines.extend(["", "Feature verification order:"])
    for feature in report["feature_test_order"]:
        queues = ", ".join(feature["queues"]) or "no BullMQ queue"
        verification = feature["verification"]
        side_effects = feature["external_side_effects"]
        external = "yes" if side_effects["can_invoke_external_systems"] else "no"
        verification_line = (
            f"    verify ({verification['owner']}): {verification['smoke_action']}"
        )
        external_line = f"    external systems: {external} ({side_effects['preflight']})"
        lines.extend(
            [
                f"{feature['sequence']:02d}. {feature['id']} [{queues}]",
                f"    requires: {', '.join(feature['requires_services'])}",
                verification_line,
                external_line,
            ]
        )
    lines.extend(
        [
            "",
            f"OpenAPI evidence: {report['api_surface']['path_count']} paths, "
            f"{report['api_surface']['operation_count']} operations, "
            f"{len(report['api_surface']['tags'])} tags.",
            "Runtime preflight is fail-closed for all external actions.",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="validate and render the side-effect-free local audit plan",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="report format (default: text)",
    )
    args = parser.parse_args(argv)
    if not args.plan_only:
        parser.error("--plan-only is required; this tool never starts runtime services")
    try:
        report = build_audit_report()
    except AuditContractError as error:
        print(error, file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text_plan(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
