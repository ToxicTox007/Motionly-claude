"""Blocking I/O stays off the API event loop.

The API runs a couple of uvicorn workers, each a single event loop. A
synchronous call inside an ``async def`` handler (the BullMQ bridge round-trip,
sync Supabase/httpx requests, DNS resolution, a stock-video download) stalls
every request that worker is serving. These tests pin the fix:

* behaviourally, a render handler whose queue blocks still lets other
  coroutines run (with a control showing the probe detects blocking);
* the rate-limit middleware never calls Supabase Auth for tokens it can reject
  locally, and offloads the call when it is needed;
* statically, no async handler calls a known-blocking API directly.
"""

import ast
import asyncio
import base64
import json
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.renders import cancel_render
from app.api.renders import create_render as create_render_api
from app.core import middleware
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.renders import RenderCreateRequest
from app.services.queue import RenderQueue
from tests.test_renders import make_render_repository, make_request

BRIDGE_DELAY_SECONDS = 0.3
TICK_SECONDS = 0.01


class _SlowBridgeQueue(RenderQueue):
    """A render queue whose bridge round-trip blocks like a stalled BullMQ bridge."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def enqueue_render(self, **kwargs):
        self.calls.append("enqueue_render")
        time.sleep(BRIDGE_DELAY_SECONDS)
        return super().enqueue_render(**kwargs)

    def cancel(self, job_id: str):
        self.calls.append("cancel")
        time.sleep(BRIDGE_DELAY_SECONDS)
        return super().cancel(job_id)


def _ticks_while(awaitable_factory) -> int:
    """How often a 10ms ticker ran while the awaitable was in flight."""

    async def scenario() -> int:
        ticks = 0
        done = False

        async def ticker() -> None:
            nonlocal ticks
            while not done:
                await asyncio.sleep(TICK_SECONDS)
                ticks += 1

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        try:
            await awaitable_factory()
        finally:
            done = True
            await task
        return ticks

    return asyncio.run(scenario())


def _create_render(repository, project_id, workspace_id, user_id, queue):
    return create_render_api(
        project_id=project_id,
        payload=RenderCreateRequest(),
        request=make_request(complete_locally=False),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        billing_repository=InMemoryBillingRepository(),
        plan_repository=InMemoryPlanRepository(repository.caption_repository),
        render_queue=queue,
    )


def _use_real_offload(monkeypatch: pytest.MonkeyPatch) -> None:
    # run_blocking runs inline under pytest; exercise the production path.
    # Called from the test body: pytest re-sets PYTEST_CURRENT_TEST at the
    # start of each phase, so removing it in a fixture would not stick.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("MOTIONLY_DISABLE_THREAD_OFFLOAD", raising=False)


# A blocked loop would tick ~0 times during the 300ms bridge call; a free one
# ticks ~30 times. The threshold sits well clear of both.
MIN_FREE_TICKS = 10


def test_render_create_and_cancel_do_not_block_the_event_loop(monkeypatch) -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    _use_real_offload(monkeypatch)
    queue = _SlowBridgeQueue()

    created = {}

    async def create():
        created["response"] = await _create_render(
            repository, project_id, workspace_id, user_id, queue
        )

    create_ticks = _ticks_while(create)

    async def cancel():
        await cancel_render(
            project_id=project_id,
            render_job_id=created["response"].job_id,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            render_queue=queue,
        )

    cancel_ticks = _ticks_while(cancel)

    assert queue.calls == ["enqueue_render", "cancel"]
    assert create_ticks >= MIN_FREE_TICKS, create_ticks
    assert cancel_ticks >= MIN_FREE_TICKS, cancel_ticks


def test_control_inline_bridge_call_starves_the_event_loop(monkeypatch) -> None:
    """Proves the probe above can fail: with offload disabled the loop stalls."""
    monkeypatch.setenv("MOTIONLY_DISABLE_THREAD_OFFLOAD", "true")
    repository, project_id, workspace_id, user_id = make_render_repository()
    queue = _SlowBridgeQueue()

    ticks = _ticks_while(
        lambda: _create_render(repository, project_id, workspace_id, user_id, queue)
    )

    assert queue.calls == ["enqueue_render"]
    assert ticks < MIN_FREE_TICKS / 2, ticks


# --------------------------------------------------------------------------
# Rate-limit middleware identity
# --------------------------------------------------------------------------


def _b64(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def _token(*, alg: str, exp_offset: int = 60) -> str:
    header = _b64({"alg": alg, "typ": "JWT"})
    payload = _b64(
        {"sub": str(uuid4()), "aud": "authenticated", "exp": int(time.time()) + exp_offset}
    )
    return f"{header}.{payload}.c2lnbmF0dXJl"


def _request(token: str):
    return SimpleNamespace(
        headers={"authorization": f"Bearer {token}"},
        state=SimpleNamespace(),
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    supabase_jwt_secret="secret",
                    supabase_url="https://project.supabase.co",
                    supabase_service_role_key="service-role",
                    app_env="test",
                )
            )
        ),
    )


def test_rate_limit_identity_does_not_call_auth_for_locally_rejected_tokens(monkeypatch):
    calls = []
    monkeypatch.setattr(
        middleware,
        "verify_supabase_token_with_auth_api",
        lambda **kwargs: calls.append(kwargs),
    )
    for token in (_token(alg="HS256"), _token(alg="HS256", exp_offset=-60), "not-a-jwt"):
        assert asyncio.run(middleware._resolve_rate_limit_identity(_request(token))) is None
    assert calls == []


def test_rate_limit_identity_offloads_auth_for_asymmetric_tokens(monkeypatch):
    _use_real_offload(monkeypatch)
    user_id = uuid4()
    threads = []

    def fake_auth_api(**kwargs):
        import threading

        threads.append(threading.current_thread() is threading.main_thread())
        return SimpleNamespace(user_id=user_id, claims={})

    monkeypatch.setattr(middleware, "verify_supabase_token_with_auth_api", fake_auth_api)
    request = _request(_token(alg="ES256"))

    identity = asyncio.run(middleware._resolve_rate_limit_identity(request))

    assert identity == f"user:{user_id}"
    assert threads == [False]
    assert request.state.verified_supabase_token.user_id == user_id


# --------------------------------------------------------------------------
# Static guard: async handlers never call known-blocking APIs directly
# --------------------------------------------------------------------------

APP = Path(__file__).resolve().parents[1] / "app"
SCANNED = [*sorted((APP / "api").glob("*.py")), APP / "core" / "middleware.py"]

BLOCKING_MODULE_CALLS = {
    ("httpx", "get"),
    ("httpx", "post"),
    ("httpx", "put"),
    ("httpx", "patch"),
    ("httpx", "delete"),
    ("httpx", "request"),
    ("socket", "getaddrinfo"),
    ("subprocess", "run"),
    ("subprocess", "check_output"),
    ("subprocess", "Popen"),
    ("time", "sleep"),
}
BLOCKING_FUNCTIONS = {
    "build_billing_status",
    "ensure_owned_stock_asset",
    "validate_render_media_manifest",
    "verify_supabase_token_with_auth_api",
    "require_workspace_membership",
}
# Queue/registry methods that go through the BullMQ bridge subprocess.
BRIDGE_METHODS = {
    "enqueue",
    "enqueue_render",
    "enqueue_plan",
    "enqueue_broll",
    "enqueue_transcription",
    "enqueue_caption_generation",
    "enqueue_video_preprocess",
    "enqueue_visual_analysis",
    "enqueue_matte",
    "enqueue_asset_enrichment",
    "enqueue_resource",
    "enqueue_music_selection",
    "enqueue_effects_config",
    "enqueue_regeneration",
    "cancel",
    "retry",
    "snapshots",
    "get_job",
    "recover_stuck_jobs",
    "mark_stuck_as_dead_letter",
}


def _blocking_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name) and func.id in BLOCKING_FUNCTIONS:
        return func.id
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and (func.value.id, func.attr) in (
            BLOCKING_MODULE_CALLS
        ):
            return f"{func.value.id}.{func.attr}"
        if func.attr in BLOCKING_FUNCTIONS:
            return func.attr
        receiver = ast.unparse(func.value).lower()
        if func.attr in BRIDGE_METHODS and ("queue" in receiver or "registry" in receiver):
            return f"{ast.unparse(func.value)}.{func.attr}"
    return None


def _direct_blocking_calls(path: Path) -> list[str]:
    findings: list[str] = []

    def visit(node: ast.AST, in_async: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                visit(child, True)
            elif isinstance(child, (ast.FunctionDef, ast.Lambda)):
                visit(child, False)
            else:
                if in_async and isinstance(child, ast.Call):
                    name = _blocking_name(child)
                    if name:
                        findings.append(f"{path.relative_to(APP)}:{child.lineno} {name}")
                visit(child, in_async)

    visit(ast.parse(path.read_text(encoding="utf-8")), False)
    return findings


def test_async_handlers_do_not_call_blocking_apis_directly() -> None:
    findings = [finding for path in SCANNED for finding in _direct_blocking_calls(path)]
    assert findings == [], (
        "Blocking call(s) inside async def; wrap them with "
        "`await run_blocking(func, ...)` (app.services.offload) or use an async API:\n"
        + "\n".join(findings)
    )


def test_static_guard_detects_a_blocking_call() -> None:
    sample = ast.parse(
        "async def handler(render_queue):\n"
        "    render_queue.enqueue_render(job=1)\n"
        "    httpx.post('https://example.com')\n"
        "    await run_blocking(render_queue.enqueue_render, job=1)\n"
    )
    calls = [node for node in ast.walk(sample) if isinstance(node, ast.Call)]
    flagged = [name for call in calls if (name := _blocking_name(call))]
    assert flagged == ["render_queue.enqueue_render", "httpx.post"]
