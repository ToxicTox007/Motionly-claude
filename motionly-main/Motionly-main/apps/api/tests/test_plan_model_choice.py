"""Task 31 / plan Section 2.11: the user's model-choice provenance is exposed on
AI-result responses (the edit-plan responses), display-only.

These tests drive the real gateway + planner bridge with a choice resolver so
``GatewayResult.model_choice`` is produced and threaded into the planner
``raw_payload['model_choice']``, then assert:

* a plan created via a *preferred* route carries ``model_choice`` with the
  ``served_option_id`` equal to the preferred option and no fallback reason;
* a plan whose preferred route fails carries the ``fallback_reason`` and the
  operator default as the served option;
* the ``ModelChoiceResult`` schema only ever carries the four safe display
  fields — never a base URL, secret or provider model id — even when the raw
  payload is polluted with operator-only keys;
* ``EditPlanRecord.to_response()`` surfaces the field on the API model.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.repositories.plans import EditPlanRecord
from app.schemas.captions import TranscriptWord
from app.schemas.plans import (
    BrollDensity,
    EditPlan,
    EditPlanStatus,
    EditPlanStyle,
    ModelChoiceResult,
    PlanCreateRequest,
    PlanSummary,
)
from app.services.model_gateway import (
    AdapterResult,
    ModelCapability,
    ModelGateway,
    ModelRoute,
    ModelUsage,
    PlanningOutput,
)
from app.services.model_provider_bridges import GatewayEditPlannerProvider
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo

WS = UUID("33333333-3333-4333-8333-333333333333")

# The operator-only secrets that must NEVER appear in any model-choice payload.
_SECRET_BASE_URL = "https://super-secret-provider.invalid/v1"
_SECRET_MODEL_ID = "super-secret-provider-model-xl"


class _Adapter:
    def __init__(self, outcomes: dict[str, AdapterResult | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def invoke(self, *, route: ModelRoute, request: object) -> AdapterResult:
        del request
        self.calls.append(route.route_id)
        outcome = self.outcomes[route.route_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _StaticResolver:
    """Returns a fixed ``(route_id, source, requested, rejected_reason)`` per ws."""

    def __init__(self, entry: tuple[str | None, str, str | None, str | None]):
        self._entry = entry

    def preference_for(self, *, operation, context):  # noqa: ANN001
        if self._entry[0] is None:
            return None
        return self._entry[0], self._entry[1]

    def resolve(self, *, operation, context):  # noqa: ANN001
        from app.services.model_choice import ResolvedChoice

        route_id, source, requested, reason = self._entry
        return ResolvedChoice(
            capability="planning.chat",
            route_id=route_id,
            choice_source=source,
            requested_option_id=requested,
            rejected_reason=reason,
        )


def _plan_payload() -> dict:
    fixture = Path(__file__).parent / "fixtures/providers/chat_completions_plan.json"
    return json.loads(fixture.read_text())


def _planning_result() -> AdapterResult:
    payload = _plan_payload()
    content = payload["choices"][0]["message"]["content"]
    return AdapterResult(
        output=PlanningOutput(content=content, raw_response=payload),
        usage=ModelUsage(input_units=120, output_units=80),
        provider_request_id="req",
    )


def _route(route_id: str, *, priority: int, selectable: bool = True) -> ModelRoute:
    # ``model`` carries the operator-only provider model id so the tests can
    # prove it never leaks into model_choice.
    return ModelRoute(
        route_id=route_id,
        capability=ModelCapability.PLANNING,
        adapter="fake",
        provider="gateway",
        model=_SECRET_MODEL_ID,
        model_revision=f"{route_id}-2026-07-14",
        priority=priority,
        selectable=selectable,
        budget_feature="planning",
    )


def _gateway(adapter: _Adapter, resolver_entry) -> ModelGateway:
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[_route("default", priority=10), _route("high", priority=20)],
    )
    gateway.set_choice_resolver(_StaticResolver(resolver_entry))
    return gateway


def _transcript():
    return SimpleNamespace(
        id=uuid4(),
        workspace_id=WS,
        project_id=uuid4(),
        updated_at=None,
        duration_ms=2000,
        raw_payload={},
        words=[
            TranscriptWord(index=0, text="Build", start_ms=0, end_ms=500, confidence=0.96),
            TranscriptWord(index=1, text="better", start_ms=550, end_ms=1100, confidence=0.95),
        ],
    )


def _create_plan(gateway: ModelGateway):
    provider = GatewayEditPlannerProvider(
        model="legacy-cache-model", provider_name="gateway", gateway=gateway
    )
    return provider.create_plan(
        transcript=_transcript(),
        payload=PlanCreateRequest(
            style="founder", target_duration_s=10, broll_density=BrollDensity.NONE
        ),
        video_duration_ms=2000,
        billing_repository=object(),
        operations_repository=object(),
    )


def _no_secret(blob: object) -> None:
    text = json.dumps(blob)
    assert _SECRET_BASE_URL not in text
    assert _SECRET_MODEL_ID not in text
    assert "secret" not in text.lower()


# ---------------------------------------------------------------------------
# Preferred route: served_option_id is the preferred option, no fallback.
# ---------------------------------------------------------------------------
def test_plan_via_preferred_route_returns_model_choice() -> None:
    adapter = _Adapter({"high": _planning_result(), "default": _planning_result()})
    gateway = _gateway(adapter, ("high", "workspace", "high", None))

    result = _create_plan(gateway)

    model_choice = result.raw_payload["model_choice"]
    assert model_choice["served_option_id"] == "high"
    assert model_choice["requested_option_id"] == "high"
    assert model_choice["choice_source"] == "workspace"
    assert model_choice["fallback_reason"] is None
    _no_secret(model_choice)

    # The typed schema parses it and the API response surfaces it.
    typed = ModelChoiceResult.from_payload(model_choice)
    assert typed is not None
    response = _record(model_choice=typed).to_response()
    assert response.model_choice is not None
    assert response.model_choice.served_option_id == "high"
    _no_secret(response.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Fallback: the preferred route fails, the default serves with a reason.
# ---------------------------------------------------------------------------
def test_plan_fallback_returns_fallback_reason() -> None:
    failure = ProviderCallFailure(
        provider="high", error_info=ProviderErrorInfo("provider_unavailable", True, 503)
    )
    adapter = _Adapter({"high": failure, "default": _planning_result()})
    gateway = _gateway(adapter, ("high", "workspace", "high", None))

    result = _create_plan(gateway)

    assert adapter.calls == ["high", "default"]
    model_choice = result.raw_payload["model_choice"]
    assert model_choice["served_option_id"] == "default"
    assert model_choice["requested_option_id"] == "high"
    assert model_choice["fallback_reason"]
    _no_secret(model_choice)

    typed = ModelChoiceResult.from_payload(model_choice)
    assert typed is not None and typed.fallback_reason
    response = _record(model_choice=typed).to_response()
    assert response.model_choice is not None
    assert response.model_choice.fallback_reason == typed.fallback_reason
    _no_secret(response.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# The schema strips operator-only / malformed data.
# ---------------------------------------------------------------------------
def test_model_choice_result_ignores_operator_only_keys() -> None:
    polluted = {
        "requested_option_id": "high",
        "served_option_id": "default",
        "choice_source": "workspace",
        "fallback_reason": "provider_unavailable",
        # Operator-only keys that might be present on a raw gateway payload must
        # not survive into the typed, client-facing model.
        "base_url": _SECRET_BASE_URL,
        "model_id": _SECRET_MODEL_ID,
        "secret_ref": "bundle:chat",
    }
    typed = ModelChoiceResult.from_payload(polluted)
    assert typed is not None
    dumped = typed.model_dump(mode="json")
    assert set(dumped) == {
        "requested_option_id",
        "served_option_id",
        "choice_source",
        "fallback_reason",
    }
    _no_secret(dumped)


def test_model_choice_result_rejects_malformed_payload() -> None:
    assert ModelChoiceResult.from_payload(None) is None
    assert ModelChoiceResult.from_payload({"served_option_id": "x"}) is None  # no source
    assert ModelChoiceResult.from_payload({"choice_source": "operator"}) is None  # no served


def test_edit_plan_response_omits_model_choice_by_default() -> None:
    # A plan created without any model-choice provenance simply omits the field.
    response = _record(model_choice=None).to_response()
    assert response.model_choice is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _record(*, model_choice: ModelChoiceResult | None) -> EditPlanRecord:
    plan = EditPlan(
        style=EditPlanStyle.MOTIVATIONAL,
        target_duration_s=10,
        cuts=[{"start_ms": 0, "end_ms": 1000, "reason": "keep"}],
        music_mood="uplifting",
        color_grade="clean_founder",
    )
    summary = PlanSummary(
        duration_s=10,
        cut_count=1,
        broll_count=0,
        zoom_count=0,
        music_mood="uplifting",
        effects=[],
        color_grade="clean_founder",
    )
    return EditPlanRecord(
        id=uuid4(),
        workspace_id=WS,
        project_id=uuid4(),
        transcript_id=uuid4(),
        version=1,
        schema_version=plan.schema_version,
        status=EditPlanStatus.READY,
        is_active=True,
        style=EditPlanStyle.MOTIVATIONAL,
        target_duration_s=10,
        broll_density=BrollDensity.NONE,
        prompt="",
        feedback=None,
        provider="gateway",
        model="model",
        cache_key="cache",
        schema_hash="hash",
        summary=summary,
        plan=plan,
        model_choice=model_choice,
    )
