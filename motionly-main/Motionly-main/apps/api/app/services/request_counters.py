from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class RequestCounters:
    supabase_calls: int = 0
    queue_calls: int = 0


current_request_counters: ContextVar[RequestCounters | None] = ContextVar(
    "current_request_counters",
    default=None,
)


def increment_supabase_calls() -> None:
    counters = current_request_counters.get()
    if counters is not None:
        counters.supabase_calls += 1


def increment_queue_calls() -> None:
    counters = current_request_counters.get()
    if counters is not None:
        counters.queue_calls += 1
