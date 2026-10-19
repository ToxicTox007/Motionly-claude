"""Browser-suite report (scripts/verify/browser_report.py).

Reads Playwright's JSON results and gates them: every setup ran, nothing
failed unexpectedly or flaked, every expected failure names the task that
fixes it. It also summarises the navigation timings each test attached, which
are the D2 browser baselines.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROJECTS = ["chromium", "firefox", "webkit", "pixel-7", "iphone-14"]


@pytest.fixture(scope="module")
def report():
    spec = importlib.util.spec_from_file_location(
        "motionly_browser_report", ROOT / "scripts" / "verify" / "browser_report.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _timings(*samples: dict) -> dict:
    body = base64.b64encode(json.dumps(list(samples)).encode()).decode()
    return {"name": "navigation-timings", "contentType": "application/json", "body": body}


def _test(project: str, status: str = "expected", *, annotations=(), duration=1000, timings=()):
    return {
        "projectName": project,
        "status": status,
        "annotations": list(annotations),
        "results": [{"duration": duration, "attachments": [_timings(*timings)] if timings else []}],
    }


def _results(tests_by_title: dict[str, list[dict]], duration: float = 60_000) -> dict:
    return {
        "stats": {"duration": duration},
        "suites": [
            {
                "title": "auth.spec.mjs",
                "specs": [
                    {"title": title, "tests": tests} for title, tests in tests_by_title.items()
                ],
                "suites": [],
            }
        ],
    }


def _all_projects(**kwargs) -> list[dict]:
    return [_test(project, **kwargs) for project in PROJECTS]


def test_a_clean_run_in_every_setup_passes(report) -> None:
    summary = report.summarize(_results({"signs in": _all_projects()}), PROJECTS)

    assert summary["problems"] == []
    assert summary["projects"]["chromium"]["tests"] == 1
    assert summary["wall_seconds"] == 60.0


def test_unexpected_failures_flakes_and_missing_setups_are_problems(report) -> None:
    results = _results(
        {
            "signs in": [_test("chromium", "unexpected"), _test("firefox", "flaky")]
            + [_test(project) for project in PROJECTS[2:4]],
        }
    )

    problems = report.summarize(results, PROJECTS)["problems"]

    assert "chromium: signs in failed" in problems
    assert "firefox: signs in is flaky" in problems
    assert "iphone-14: no tests ran" in problems


def test_skipped_tests_are_left_to_the_skip_audit(report) -> None:
    results = _results({"needs gpu": _all_projects(status="skipped")})

    summary = report.summarize(results, PROJECTS)

    assert summary["problems"] == []
    assert summary["projects"]["webkit"]["skipped"] == 1


def test_an_expected_failure_must_name_the_task_that_fixes_it(report) -> None:
    marked = [{"type": "fail", "description": "Task 15: preprocessing fails before the editor"}]
    unmarked = [{"type": "fail", "description": "broken for now"}]
    results = _results(
        {
            "reaches the editor": _all_projects(annotations=marked),
            "renders": _all_projects(annotations=unmarked),
        }
    )

    summary = report.summarize(results, PROJECTS)

    assert summary["expected_failures"] == [
        {"test": "reaches the editor", "reason": "Task 15: preprocessing fails before the editor"},
        {"test": "renders", "reason": "broken for now"},
    ]
    assert summary["problems"] == [
        f"{project}: renders is expected to fail without naming its task" for project in PROJECTS
    ]


def test_navigation_timings_are_summarised_per_setup_and_page(report) -> None:
    def nav(path: str, load: int) -> dict:
        return {
            "path": path,
            "ttfb_ms": load // 4,
            "dom_content_loaded_ms": load // 2,
            "load_ms": load,
        }

    results = _results(
        {
            "opens projects": [
                _test(
                    "chromium",
                    timings=(
                        nav("/projects/0b2f0b8e-1c1e-4c5e-9a43-3d2b9f0f6a10", 400),
                        nav("/login", 200),
                    ),
                ),
                _test("firefox", timings=(nav("/login", 600),)),
            ]
            + [_test(project) for project in PROJECTS[2:]],
        }
    )

    timings = report.summarize(results, PROJECTS)["navigation"]

    assert timings["by_project"]["chromium"]["load_ms"] == {
        "count": 2,
        "p50": 300,
        "p95": 390,
        "max": 400,
    }
    assert timings["by_path"]["/projects/:id"]["load_ms"]["max"] == 400
    assert timings["by_path"]["/login"]["load_ms"] == {
        "count": 2,
        "p50": 400,
        "p95": 580,
        "max": 600,
    }
    assert "webkit" not in timings["by_project"]
