"""CPU-stage evidence report (scripts/verify/evidence_report.py).

Maps each of the 10 Definition-of-done items to the verify stage and tests that
prove it. A stage that did not run in this environment marks its item
``not_run`` — never ``passed``. Records what each fixture-backed capability
proved and did not, the GPU-stage and optional checks not run, and the open
decisions.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def evidence():
    spec = importlib.util.spec_from_file_location(
        "motionly_evidence_report", ROOT / "scripts" / "verify" / "evidence_report.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stages() -> dict:
    return {
        "stages": [
            {"name": "static"},
            {"name": "unit"},
            {"name": "db"},
            {"name": "contracts"},
            {"name": "media"},
            {"name": "stack"},
            {"name": "browser"},
            {"name": "leakage"},
            {"name": "aws"},
            {"name": "deadcode"},
            {"name": "perf"},
        ]
    }


def _registry() -> dict:
    return {
        "entries": [
            {
                "id": "music",
                "kind": "fixture",
                "stands_in_for": "The licensed music catalog",
                "production": "rejected",
            },
            {
                "id": "mock-payment-gateway",
                "kind": "demo",
                "stands_in_for": "A real UPI + card payment gateway",
                "production": "demo_mode_only",
            },
        ]
    }


def _gpu() -> dict:
    return {
        "gpu_stage": [
            {
                "id": "release-compose-integration",
                "kind": "ci_job",
                "reason": "needs a GPU runner",
                "location": "gpu-stage.yml",
            }
        ],
        "optional": [
            {
                "id": "live-provider-contracts",
                "reason": "calls real providers",
                "enable_env": "RUN_LIVE_PROVIDER_CONTRACTS",
            }
        ],
    }


def _summary(**stage_statuses) -> dict:
    return {
        "result": stage_statuses.pop("result", None),
        "started_at": "s",
        "finished_at": "f",
        "stages": [{"name": name, "status": status} for name, status in stage_statuses.items()],
    }


def _item(report: dict, item_id: int) -> dict:
    return next(i for i in report["definition_of_done"] if i["id"] == item_id)


def test_a_stage_that_did_not_run_marks_its_item_not_run_never_passed(evidence) -> None:
    # db ran and passed; leakage ran and passed; everything else absent.
    summary = _summary(db="passed", leakage="passed", result="passed")
    report = evidence.build_evidence(_stages(), summary, _registry(), _gpu())

    assert _item(report, 2)["status"] == "passed"  # db
    assert _item(report, 6)["status"] == "passed"  # leakage
    for item_id in (3, 4, 5, 7, 8, 9, 10):  # stack/browser/aws/deadcode/perf — not in summary
        assert _item(report, item_id)["status"] == "not_run", item_id


def test_a_failed_stage_marks_its_item_failed(evidence) -> None:
    summary = _summary(db="failed", result="failed")
    report = evidence.build_evidence(_stages(), summary, _registry(), _gpu())

    assert _item(report, 2)["status"] == "failed"


def test_item_one_passes_only_when_every_stage_ran_and_passed(evidence) -> None:
    # Only db ran -> item 1 is not_run, even though the db-only run "passed".
    partial = evidence.build_evidence(
        _stages(), _summary(db="passed", result="passed"), _registry(), _gpu()
    )
    assert _item(partial, 1)["status"] == "not_run"

    # Every stage ran and passed -> item 1 passes.
    all_passed = {name: "passed" for name in [s["name"] for s in _stages()["stages"]]}
    full = evidence.build_evidence(
        _stages(), _summary(result="passed", **all_passed), _registry(), _gpu()
    )
    assert _item(full, 1)["status"] == "passed"

    # A failed stage makes item 1 failed.
    failed = {**all_passed, "aws": "failed"}
    broken = evidence.build_evidence(
        _stages(), _summary(result="failed", **failed), _registry(), _gpu()
    )
    assert _item(broken, 1)["status"] == "failed"


def test_every_dod_item_is_present_and_maps_to_a_stage(evidence) -> None:
    report = evidence.build_evidence(_stages(), _summary(), _registry(), _gpu())
    ids = [i["id"] for i in report["definition_of_done"]]
    assert ids == list(range(1, 11))
    for item in report["definition_of_done"]:
        assert item["statement"].strip()
        assert item["proving_stage"]


def test_fixture_capabilities_record_proved_not_proved_and_where_validated(evidence) -> None:
    report = evidence.build_evidence(_stages(), _summary(), _registry(), _gpu())
    by_id = {f["id"]: f for f in report["fixture_backed_capabilities"]}

    assert "music" in by_id and "mock-payment-gateway" in by_id
    music = by_id["music"]
    assert music["cpu_stage_proved"]
    assert music["cpu_stage_did_not_prove"]
    assert "catalog" in music["validated_at"].lower() or "licens" in music["validated_at"].lower()


def test_gpu_and_optional_checks_not_run_are_listed(evidence) -> None:
    report = evidence.build_evidence(_stages(), _summary(), _registry(), _gpu())

    assert [c["id"] for c in report["gpu_stage_checks_not_run"]] == [
        "release-compose-integration"
    ]
    assert [c["id"] for c in report["optional_live_checks_not_run"]] == [
        "live-provider-contracts"
    ]


def test_open_decisions_cover_d1_through_d4(evidence) -> None:
    report = evidence.build_evidence(_stages(), _summary(), _registry(), _gpu())
    ids = {d["id"] for d in report["open_decisions"]}
    assert {"D1", "D2", "D3", "D4"} <= ids


def test_a_stage_missing_from_stages_json_is_flagged(evidence) -> None:
    stages = {"stages": [{"name": "db"}]}  # no perf/browser/etc.
    report = evidence.build_evidence(stages, _summary(db="passed"), _registry(), _gpu())

    perf_item = _item(report, 9)
    assert perf_item["status"] == "not_run"
    assert "warning" in perf_item


def test_junit_tests_are_attached_when_the_report_exists(evidence, tmp_path: Path) -> None:
    junit_dir = tmp_path / "junit"
    junit_dir.mkdir()
    (junit_dir / "leakage-api.xml").write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_fixture_leakage" name="test_registered"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    report = evidence.build_evidence(
        _stages(),
        _summary(leakage="passed"),
        _registry(),
        _gpu(),
        junit_dir=junit_dir,
        browser_junit=tmp_path / "missing.xml",
    )
    leakage = _item(report, 6)
    assert "tests.test_fixture_leakage::test_registered" in leakage["tests"]


def test_run_writes_the_evidence_file(evidence, tmp_path: Path) -> None:
    stages = tmp_path / "stages.json"
    stages.write_text(json.dumps(_stages()), encoding="utf-8")
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(_summary(db="passed", result="passed")), encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(_registry()), encoding="utf-8")
    gpu = tmp_path / "gpu.json"
    gpu.write_text(json.dumps(_gpu()), encoding="utf-8")
    out = tmp_path / "evidence.json"

    rc = evidence.main(
        ["--stages", str(stages), "--summary", str(summary), "--registry", str(registry),
         "--gpu-manifest", str(gpu), "--output", str(out)]
    )
    assert rc == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["schema_version"] == 1
    assert len(written["definition_of_done"]) == 10


def test_a_missing_summary_makes_every_runnable_item_not_run(evidence, tmp_path: Path) -> None:
    stages = tmp_path / "stages.json"
    stages.write_text(json.dumps(_stages()), encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(_registry()), encoding="utf-8")
    gpu = tmp_path / "gpu.json"
    gpu.write_text(json.dumps(_gpu()), encoding="utf-8")
    out = tmp_path / "evidence.json"

    evidence.run(
        stages, tmp_path / "no-summary.json", registry, gpu, out
    )
    written = json.loads(out.read_text(encoding="utf-8"))
    assert all(i["status"] == "not_run" for i in written["definition_of_done"])


def _decision(report: dict, decision_id: str) -> dict:
    return next(d for d in report["open_decisions"] if d["id"] == decision_id)


def test_d1_records_node_24_as_approved_and_implemented(evidence) -> None:
    d1 = _decision(evidence.build_evidence(_stages(), _summary(), _registry(), _gpu()), "D1")
    assert d1["status"] == "approved"
    assert "Node 24" in d1["detail"]
    assert "test_node_runtime_contract.py" in d1["detail"]


def test_d2_names_every_service_still_pending_a_measured_baseline(evidence) -> None:
    budget = json.loads((ROOT / "tests" / "cpu-stage" / "resource-budget.json").read_text("utf-8"))
    pending = sorted(n for n, e in budget["services"].items() if e.get("pending_baseline"))
    d2 = _decision(evidence.build_evidence(_stages(), _summary(), _registry(), _gpu()), "D2")
    assert pending == ["payment-gateway", "supabase-realtime"]
    assert d2["status"] == "approved-with-pending-baselines"
    for name in pending:
        assert name in d2["detail"], name


def test_d3_records_the_approved_licence_policy_and_pending_track_signoff(evidence) -> None:
    licences = json.loads(
        (ROOT / "apps" / "api" / "app" / "config" / "music-licenses.json").read_text("utf-8")
    )
    d3 = _decision(evidence.build_evidence(_stages(), _summary(), _registry(), _gpu()), "D3")
    assert licences["approved"] is True
    assert d3["status"] == "policy-approved-track-signoff-pending"
    assert "CC0 1.0" in d3["detail"]
    assert "no real tracks" in d3["detail"].lower()
