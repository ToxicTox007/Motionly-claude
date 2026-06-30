from contextvars import ContextVar

current_trace_id: ContextVar[str | None] = ContextVar("motionly_trace_id", default=None)
