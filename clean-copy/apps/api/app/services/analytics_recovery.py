import json
from collections import deque
from typing import Any
from uuid import UUID

from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer


class AnalyticsRecoveryQueue:
    KEY = "motionly:analytics-recovery"

    def __init__(self, redis_url: str | None = None) -> None:
        self._memory: deque[str] = deque(maxlen=10_000)
        self._redis = None
        if redis_url:
            from redis import Redis

            self._redis = Redis.from_url(redis_url, decode_responses=True)

    def enqueue(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, default=str, separators=(",", ":"))
        if self._redis is not None:
            self._redis.rpush(self.KEY, encoded)
            return
        self._memory.append(encoded)

    def drain(self, repository, *, limit: int = 250) -> int:
        recovered = 0
        for _ in range(limit):
            encoded = self._pop()
            if encoded is None:
                break
            payload = json.loads(encoded)
            try:
                repository.record_event(
                    workspace_id=UUID(payload["workspace_id"]),
                    project_id=UUID(payload["project_id"]),
                    user_id=UUID(payload["user_id"]),
                    edit_plan_id=UUID(payload["edit_plan_id"])
                    if payload.get("edit_plan_id")
                    else None,
                    event_type=AIEditFeedbackEventType(payload["event_type"]),
                    layer=AIEditLayer(payload["layer"]),
                    source=payload["source"],
                    decision_id=payload.get("decision_id"),
                    action=payload.get("action"),
                    ai_payload=payload.get("ai_payload"),
                    user_payload=payload.get("user_payload"),
                    metadata=payload.get("metadata"),
                )
            except Exception:
                self.enqueue(payload)
                break
            recovered += 1
        return recovered

    def pending_count(self) -> int:
        if self._redis is not None:
            return int(self._redis.llen(self.KEY))
        return len(self._memory)

    def _pop(self) -> str | None:
        if self._redis is not None:
            value = self._redis.lpop(self.KEY)
            return str(value) if value is not None else None
        return self._memory.popleft() if self._memory else None
