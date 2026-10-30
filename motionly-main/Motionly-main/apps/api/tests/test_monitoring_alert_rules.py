"""The shipped alert rules only use metrics the API exports (audit WJ-20)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
RULES = ROOT / "infra" / "monitoring" / "prometheus-alerts.yml"
METRICS_SOURCE = ROOT / "apps" / "api" / "app" / "api" / "operations.py"


def test_alert_rules_parse_and_reference_exported_metrics() -> None:
    document = yaml.safe_load(RULES.read_text())
    rules = [rule for group in document["groups"] for rule in group["rules"]]
    assert rules, "alert rules are defined"
    exported = set(re.findall(r"motionly_[a-z_]+", METRICS_SOURCE.read_text()))
    for rule in rules:
        assert rule["alert"].startswith("Motionly")
        assert rule["labels"]["severity"] in {"warning", "critical"}
        used = set(re.findall(r"motionly_[a-z_]+", rule["expr"]))
        assert used, f"{rule['alert']} uses a Motionly metric"
        assert used <= exported, f"{rule['alert']} uses unexported metrics: {used - exported}"
    names = {rule["alert"] for rule in rules}
    assert {"MotionlyQueueWithoutWorker", "MotionlyQueueDeadLetters"} <= names
