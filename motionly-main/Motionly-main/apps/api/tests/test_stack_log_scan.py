"""Service-log scan run after the browser suite (scripts/verify/cpu_stage_stack.py logscan).

A browser test can pass while a service fails behind it (a background job, a
swallowed exception, a 5xx the page never showed). The scan reads every CPU
stage service's logs since the suite started and fails on errors.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = "172.21.0.8 - - [07/Oct/2026:07:21:01 +0000] "


@pytest.fixture(scope="module")
def checker():
    spec = importlib.util.spec_from_file_location(
        "motionly_cpu_stage_stack_logscan", ROOT / "scripts" / "verify" / "cpu_stage_stack.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("service", "line"),
    [
        ("api", "Traceback (most recent call last):"),
        ("api", '{"asctime": "x", "levelname": "ERROR", "name": "app", "message": "boom"}'),
        ("api", '{"levelname": "CRITICAL", "message": "down"}'),
        ("api", 'INFO:     172.21.0.14:39828 - "GET /projects HTTP/1.1" 500 Internal Server Error'),
        ("supabase-gateway", GATEWAY + '"POST /rest/v1/rpc/x HTTP/1.1" 502 12 "-" "httpx"'),
        ("edge", '{"component":"cdn-sim","path":"/api/x","status":504,"cache":"error"}'),
        ("supabase-auth", '{"level":"error","msg":"database error"}'),
        ("supabase-auth", '{"level":"fatal","msg":"cannot start"}'),
        ("temporal", '{"level":"error","msg":"Queue reader unable to retrieve tasks"}'),
        ("postgres", "2026-10-07 07:00:00.000 UTC [77] ERROR:  duplicate key value"),
        ("postgres", "2026-10-07 07:00:00.000 UTC [77] FATAL:  password authentication failed"),
        (
            "postgres",
            "172.21.0.6 2026-10-07 07:40:50.716 UTC [10092] authenticator@postgres ERROR:  X",
        ),
        ("web", " ⨯ Error: fetch failed"),
        ("web", "Error: Cannot read properties of undefined"),
        ("cpu-worker", '{"level":"error","msg":"job failed"}'),
        # Storage logs with pino: 50 is error, 60 fatal.
        ("supabase-storage", '{"level":50,"msg":"delete failed","res":{"statusCode":500}}'),
        ("supabase-storage", '{"level":60,"msg":"cannot start"}'),
        ("remotion-worker", "Unhandled promise rejection: Error: render failed"),
        # Node workers log failures as JSON events without a level.
        ("cpu-worker", '{"event":"job_failed","kind":"video_preprocess","error_code":"x"}'),
        ("remotion-worker", '{"event":"worker_error","message":"boom"}'),
        ("remotion-worker", '{"event":"job_stalled","queue_job_id":"x"}'),
        ("cpu-worker", '{"event":"job_requeued_after_subprocess_crash"}'),
        ("cpu-worker", '{"event":"worker_heartbeat_failed"}'),
    ],
)
def test_errors_in_any_service_log_are_reported(checker, service: str, line: str) -> None:
    assert checker.log_problems(service, line) == [f"{service}: {line.strip()}"]


@pytest.mark.parametrize(
    ("service", "line"),
    [
        ("api", '{"levelname": "INFO", "message": "api_request_completed"}'),
        ("api", '{"levelname": "WARNING", "message": "api_slow_request"}'),
        ("api", 'INFO:     172.21.0.14:39828 - "POST /internal/heartbeat HTTP/1.1" 200 OK'),
        ("api", 'INFO:     172.21.0.14:39828 - "GET /projects/x HTTP/1.1" 404 Not Found'),
        ("supabase-gateway", GATEWAY + '"POST /auth/v1/token HTTP/1.1" 400 90 "-" "x"'),
        ("edge", '{"component":"cdn-sim","method":"GET","path":"/login","status":307}'),
        # Auth logs rejected requests at info level, with an "error" field.
        ("supabase-auth", '{"error":"400: Invalid login credentials","level":"info","msg":"x"}'),
        ("temporal", '{"level":"info","msg":"matching client encountered error"}'),
        ("postgres", "2026-10-07 07:00:00.000 UTC [77] LOG:  checkpoint complete"),
        # Function bodies postgres echoes in a statement log are not errors.
        ("postgres", "\t    raise exception 'WORKFLOW_TERMINAL' using errcode = 'P0001';"),
        ("web", "motionly_login_failed {"),
        ("supabase-storage", '{"level":30,"msg":"request completed"}'),
        ("supabase-storage", '{"level":40,"msg":"Object not found"}'),
        ("web", "✓ Ready in 535ms"),
        ("cpu-worker", '{"event":"job_started","kind":"video_preprocess"}'),
        ("cpu-worker", '{"event":"job_runtime_metrics","success":false}'),
    ],
)
def test_ordinary_log_lines_are_not_reported(checker, service: str, line: str) -> None:
    assert checker.log_problems(service, line) == []


def test_multi_line_logs_report_each_error_line(checker) -> None:
    text = "\n".join(
        [
            '{"levelname": "INFO", "message": "ok"}',
            "Traceback (most recent call last):",
            '  File "x.py", line 1, in <module>',
            "ValueError: bad",
            '{"levelname": "INFO", "message": "ok"}',
            '{"levelname": "ERROR", "message": "boom"}',
        ]
    )
    assert checker.log_problems("api", text) == [
        "api: Traceback (most recent call last): … ValueError: bad",
        'api: {"levelname": "ERROR", "message": "boom"}',
    ]


def test_a_traceback_is_reported_with_the_exception_it_ends_in(checker) -> None:
    text = "\n".join(
        [
            "Traceback (most recent call last):",
            '  File "/app/activities.py", line 79, in enqueue',
            "    raise ApplicationError(",
            "temporalio.exceptions.ApplicationError: bullmq_enqueue_unavailable: unavailable.",
            "Completing activity as failed",
        ]
    )
    assert checker.log_problems("temporal-worker", text) == [
        "temporal-worker: Traceback (most recent call last): … "
        "temporalio.exceptions.ApplicationError: bullmq_enqueue_unavailable: unavailable."
    ]


def test_known_defects_are_separated_from_new_problems(checker) -> None:
    known = [
        {
            "id": "step-retry-conflict",
            "task": "Task 15",
            "service": "postgres",
            "pattern": r"ERROR:\s+WORKFLOW_STEP_TRANSPORT_CONFLICT",
            "reason": "A failed first-draft step cannot be enqueued again.",
        }
    ]
    problems = [
        "postgres: UTC [1] authenticator@postgres ERROR:  WORKFLOW_STEP_TRANSPORT_CONFLICT",
        "api: Traceback (most recent call last): … ValueError: boom",
        # The same text in another service is not the known defect.
        "api: ERROR:  WORKFLOW_STEP_TRANSPORT_CONFLICT",
    ]

    new, matched = checker.classify_log_problems(problems, known)

    assert new == problems[1:]
    assert matched == {"step-retry-conflict": 1}


def test_every_registered_known_defect_names_its_task_and_a_valid_pattern(checker) -> None:
    import json
    import re

    register = json.loads(
        (ROOT / "tests" / "cpu-stage" / "known-log-defects.json").read_text(encoding="utf-8")
    )
    assert register["schema_version"] == 1
    for entry in register["defects"]:
        assert set(entry) == {"id", "task", "service", "pattern", "reason"}, entry
        assert re.fullmatch(r"Task \d+", entry["task"]), entry
        re.compile(entry["pattern"])
        assert len(entry["reason"]) > 20, entry


def test_logscan_is_a_command_with_a_start_time(checker) -> None:
    parser_help = checker.build_parser().format_help()
    assert "logscan" in parser_help
    args = checker.build_parser().parse_args(["logscan", "--since", "2026-10-07T06:00:00Z"])
    assert args.since == "2026-10-07T06:00:00Z"


def test_the_log_scan_can_start_at_a_named_mark(checker) -> None:
    parser = checker.build_parser()
    mark = parser.parse_args(["mark", "browser"])
    scan = parser.parse_args(["logscan", "--since-mark", "browser"])
    state = {"marks": {"browser": "2026-10-07T08:00:00Z"}}

    assert mark.name == "browser"
    assert checker.scan_start(state, scan) == "2026-10-07T08:00:00Z"
    assert checker.scan_start(
        state, parser.parse_args(["logscan", "--since", "2026-10-07T09:00:00Z"])
    ) == ("2026-10-07T09:00:00Z")
    with pytest.raises(checker.StackError, match="no mark named 'other'"):
        checker.scan_start(state, parser.parse_args(["logscan", "--since-mark", "other"]))
    with pytest.raises(SystemExit):
        parser.parse_args(["logscan"])


def test_up_returns_only_once_the_api_is_ready_through_the_edge(checker, monkeypatch) -> None:
    # Compose cannot wait for the API (no container health check until Task 9),
    # so `up` polls readiness through the edge before the browser suite starts.
    answers = iter(
        [
            (502, "{}"),
            (200, '{"status":"ready","environment":"production"}'),
            (200, '{"status":"ready","environment":"cpu-stage"}'),
        ]
    )
    calls = []

    def fake_http(url, **_kwargs):
        calls.append(url)
        return next(answers)

    monkeypatch.setattr(checker, "http", fake_http)
    monkeypatch.setattr(checker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(checker, "trust_edge_ca", lambda _state: None)

    checker.wait_for_api_ready({})

    assert calls == [f"{checker.STACK['public_origin']}/api/health/ready"] * 3
    source = (ROOT / "scripts" / "verify" / "cpu_stage_stack.py").read_text(encoding="utf-8")
    up = source[source.index("def cmd_up") : source.index("def _migration_versions")]
    assert up.rstrip().endswith("wait_for_api_ready(state)") or "wait_for_api_ready(state)" in up


def _temporal(ts: str, level: str, msg: str, queue: str, **extra: str) -> str:
    import json

    return json.dumps(
        {
            "level": level,
            "ts": ts,
            "msg": msg,
            "component": "matching-engine",
            "wf-task-queue-name": queue,
            "wf-namespace": "temporal-system",
            **extra,
        }
    )


def _cancelled_ack_save(ts: str, queue: str, error: str = "context canceled") -> str:
    return _temporal(
        ts,
        "error",
        "Persistent store operation failure",
        queue,
        **{"store-operation": "update-task-queue", "error": error},
    )


def test_temporal_cancelling_the_ack_save_of_a_queue_it_just_unloaded_is_not_a_problem(
    checker,
) -> None:
    """Found by the browser stage: Temporal 1.27.1 unloads idle task-queue
    partitions after 5 minutes; when its periodic ack-level save races the
    unload, it logs the cancelled save at error level ("keep going as saving
    ack is not critical", service/matching/task_reader.go)."""
    queue = "/_sys/default-worker-tq/2"
    text = "\n".join(
        [
            _temporal(
                "2026-10-19T01:59:17.258Z", "info", "Stopped physicalTaskQueueManager", queue
            ),
            _cancelled_ack_save("2026-10-19T01:59:17.264Z", queue),
        ]
    )
    ignored: list[str] = []

    assert checker.log_problems("temporal", text, ignored=ignored) == []
    assert len(ignored) == 1 and queue in ignored[0]


@pytest.mark.parametrize(
    "lines",
    [
        # No unload of that queue.
        [_cancelled_ack_save("2026-10-19T01:59:17.264Z", "/_sys/default-worker-tq/2")],
        # A different queue was unloaded.
        [
            _temporal(
                "2026-10-19T01:59:17.258Z", "info", "Stopped physicalTaskQueueManager", "other"
            ),
            _cancelled_ack_save("2026-10-19T01:59:17.264Z", "/_sys/default-worker-tq/2"),
        ],
        # A real persistence failure, not a cancellation.
        [
            _temporal("2026-10-19T01:59:17.258Z", "info", "Stopped physicalTaskQueueManager", "q"),
            _cancelled_ack_save("2026-10-19T01:59:17.264Z", "q", error="context deadline exceeded"),
        ],
        # Long after the unload.
        [
            _temporal("2026-10-19T01:59:17.258Z", "info", "Stopped physicalTaskQueueManager", "q"),
            _cancelled_ack_save("2026-10-19T02:09:17.264Z", "q"),
        ],
    ],
)
def test_other_temporal_store_failures_are_still_problems(checker, lines: list[str]) -> None:
    assert len(checker.log_problems("temporal", "\n".join(lines))) == 1


def test_the_unload_rule_applies_to_temporal_only(checker) -> None:
    queue = "q"
    text = "\n".join(
        [
            _temporal(
                "2026-10-19T01:59:17.258Z", "info", "Stopped physicalTaskQueueManager", queue
            ),
            _cancelled_ack_save("2026-10-19T01:59:17.264Z", queue),
        ]
    )
    assert len(checker.log_problems("api", text)) == 1


def test_auth_requests_without_the_viewer_address_are_problems(checker) -> None:
    """GoTrue skips its rate limit, and says so, when a rate-limited request
    lacks the configured header. That is a fail-open misconfiguration (for
    example a server-side Auth call that does not pass the address on)."""
    line = (
        '{"component":"api","header":"X-Motionly-Viewer-IP","level":"warning",'
        '"method":"POST","msg":"request does not have a value for the rate limiting header,'
        ' rate limiting is not applied","path":"/token","time":"2026-10-19T09:00:00Z"}'
    )
    assert checker.log_problems("supabase-auth", line) == [f"supabase-auth: {line}"]


def test_the_stack_check_proves_auth_limits_each_viewer_separately(checker, monkeypatch) -> None:
    """The check speaks to the origin as CloudFront does (with the origin
    secret), naming the viewer in CloudFront-Viewer-Address. One viewer, on
    changing source ports, is limited; another viewer is not."""
    calls: list[dict[str, str]] = []
    budget = {"198.51.100.20": 40}

    def fake_http_json(method, url, *, headers, body=None):
        assert method == "POST" and url.endswith("/supabase/auth/v1/token?grant_type=password")
        assert url.startswith(checker.STACK["origin_direct"])
        calls.append(dict(headers))
        viewer = headers["CloudFront-Viewer-Address"].rsplit(":", 1)[0]
        if viewer in budget:
            budget[viewer] -= 1
            if budget[viewer] < 0:
                return 429, {"error_code": "over_request_rate_limit"}
        return 400, {"error_code": "invalid_credentials"}

    monkeypatch.setattr(checker, "_http_json", fake_http_json)
    monkeypatch.setattr(checker, "_stack_env", lambda _state: {"SUPABASE_ANON_KEY": "anon"})
    monkeypatch.setattr(checker, "secret_path", lambda _state, _name: _Secret("s" * 43))

    result = checker.check_auth_rate_limit({})

    limited = [c for c in calls if c["CloudFront-Viewer-Address"].startswith("198.51.100.20:")]
    other = [c for c in calls if c["CloudFront-Viewer-Address"].startswith("198.51.100.21:")]
    assert len({c["CloudFront-Viewer-Address"] for c in limited}) == len(limited), "ports vary"
    assert len(limited) == 41 and len(other) == 1
    assert all(c[checker.ORIGIN_GUARD_HEADER] == "s" * 43 for c in calls)
    assert result == {"limited_after": 40, "other_viewer_limited": False}


def test_the_rate_limit_check_fails_when_no_viewer_is_ever_limited(checker, monkeypatch) -> None:
    monkeypatch.setattr(
        checker, "_http_json", lambda *_a, **_k: (400, {"error_code": "invalid_credentials"})
    )
    monkeypatch.setattr(checker, "_stack_env", lambda _state: {"SUPABASE_ANON_KEY": "anon"})
    monkeypatch.setattr(checker, "secret_path", lambda _state, _name: _Secret("s" * 43))
    with pytest.raises(checker.StackError, match="never rate-limited"):
        checker.check_auth_rate_limit({})


def test_the_rate_limit_check_runs_with_the_stack_checks(checker) -> None:
    import inspect

    assert "check_auth_rate_limit(state)" in inspect.getsource(checker.cmd_check)


class _Secret:
    def __init__(self, value: str) -> None:
        self.value = value

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.value + "\n"
