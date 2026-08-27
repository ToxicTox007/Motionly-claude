from __future__ import annotations

import multiprocessing
import os
import time
from uuid import uuid4

import pytest

from app.services.model_gateway import (
    BudgetReservation,
    ModelBudgetExceeded,
    RedisBudgetLedger,
    RedisCircuitStore,
    SharedCircuitOpen,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REDIS_CONTRACTS") != "true",
    reason="set RUN_REDIS_CONTRACTS=true and REDIS_URL to run real Redis contracts",
)


def _reserve_in_process(
    redis_url: str,
    prefix: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
    amount: float,
    limit: float,
    reservation_ttl_seconds: int = 60,
) -> None:
    ledger = RedisBudgetLedger(
        redis_url,
        prefix=prefix,
        reservation_ttl_seconds=reservation_ttl_seconds,
    )
    start.wait(timeout=10)
    try:
        reservation = ledger.reserve(scope="workspace:2026-07", amount_usd=amount, limit_usd=limit)
        results.put(("accepted", reservation.token, reservation.reserved_usd))
    except ModelBudgetExceeded:
        results.put(("rejected", None, None))


def _open_circuit_in_process(redis_url: str, prefix: str, route_key: str) -> None:
    store = RedisCircuitStore(
        redis_url,
        prefix=prefix,
        failure_threshold=1,
        cooldown_seconds=30,
    )
    store.record_failure(route_key, retryable=True)


def _cleanup(redis_url: str, prefix: str) -> None:
    from redis import Redis

    redis = Redis.from_url(redis_url, decode_responses=True)
    keys = list(redis.scan_iter(f"{prefix}*"))
    if keys:
        redis.delete(*keys)


def test_real_redis_budget_is_atomic_across_processes_at_exact_cap() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/14")
    prefix = f"motionly:test-budget:{uuid4().hex}"
    context = multiprocessing.get_context("fork")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_reserve_in_process,
            args=(redis_url, prefix, start, results, 0.5, 1.0),
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        outcomes = [results.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0

        assert [outcome[0] for outcome in outcomes].count("accepted") == 2
        ledger = RedisBudgetLedger(redis_url, prefix=prefix)
        with pytest.raises(ModelBudgetExceeded):
            ledger.reserve(scope="workspace:2026-07", amount_usd=0.01, limit_usd=1.0)

        for _status, token, amount in outcomes:
            assert token is not None and amount is not None
            ledger.release(BudgetReservation(token=token, reserved_usd=amount, limit_usd=1.0))
        reservation = ledger.reserve(
            scope="workspace:2026-07",
            amount_usd=1.0,
            limit_usd=1.0,
        )
        ledger.release(reservation)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
        _cleanup(redis_url, prefix)


def test_real_redis_over_reservation_settlement_charges_full_actual_before_raising() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/14")
    prefix = f"motionly:test-over-reservation:{uuid4().hex}"
    scope = "workspace:2026-07"
    ledger = RedisBudgetLedger(redis_url, prefix=prefix)
    try:
        reservation = ledger.reserve(scope=scope, amount_usd=1.0, limit_usd=10.0)

        with pytest.raises(ModelBudgetExceeded, match="reservation"):
            ledger.commit(reservation, actual_usd=2.0)

        assert ledger.usage(scope) == pytest.approx(2.0)
        with pytest.raises(ModelBudgetExceeded, match="exhausted"):
            ledger.reserve(scope=scope, amount_usd=9.0, limit_usd=10.0)
    finally:
        _cleanup(redis_url, prefix)


def test_real_redis_reclaims_expired_crash_reservation_on_next_reserve() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/14")
    prefix = f"motionly:test-lease:{uuid4().hex}"
    context = multiprocessing.get_context("fork")
    start = context.Event()
    results = context.Queue()
    process = context.Process(
        target=_reserve_in_process,
        args=(redis_url, prefix, start, results, 1.0, 1.0, 1),
    )
    try:
        process.start()
        start.set()
        assert results.get(timeout=10)[0] == "accepted"
        process.join(timeout=10)
        assert process.exitcode == 0

        time.sleep(2.0)
        ledger = RedisBudgetLedger(redis_url, prefix=prefix, reservation_ttl_seconds=1)
        recovered = ledger.reserve(
            scope="workspace:2026-07",
            amount_usd=1.0,
            limit_usd=1.0,
        )
        ledger.release(recovered)
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        _cleanup(redis_url, prefix)


def test_real_redis_circuit_state_is_shared_across_processes_by_revision() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/14")
    prefix = f"motionly:test-circuit:{uuid4().hex}"
    route_key = "planning-primary:planner-2026-07-14"
    context = multiprocessing.get_context("fork")
    process = context.Process(
        target=_open_circuit_in_process,
        args=(redis_url, prefix, route_key),
    )
    try:
        process.start()
        process.join(timeout=10)
        assert process.exitcode == 0

        store = RedisCircuitStore(
            redis_url,
            prefix=prefix,
            failure_threshold=1,
            cooldown_seconds=30,
        )
        with pytest.raises(SharedCircuitOpen):
            store.check(route_key)
        store.check("planning-primary:planner-2026-07-15")
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        _cleanup(redis_url, prefix)
