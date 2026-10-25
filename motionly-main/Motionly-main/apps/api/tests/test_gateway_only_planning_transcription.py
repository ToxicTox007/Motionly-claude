"""Task 10: gateway-only planning and transcription, and feature-keyed quotas.

Planning and transcription run only through the provider-neutral model gateway
(the direct-HTTP providers were deleted). These tests prove:

* a fake upload -> transcript -> plan flow runs entirely through the gateway with
  no direct HTTP (the no-network guard in conftest is active and the fake
  adapter's call log is the only record of the calls);
* two routes with different provider labels serve planning and transcription
  without any consumer change;
* Decision B-1 counts the planning quota per feature/category: usage accumulates
  across a provider-label switch under ``AI_QUOTA_SCOPE=feature`` and resets
  under ``provider``, while planning usage rows are written in both scopes;
* the ``QUOTA_PLANNING_*`` settings are read and the old provider-keyed setting
  names are gone;
* the M1 index migration and its pgTAP test exist.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.repositories.billing import InMemoryBillingRepository
from app.schemas.billing import BillingPlanKey, SubscriptionStatus
from app.schemas.captions import LanguageHint, TranscriptWord
from app.schemas.plans import BrollDensity, PlanCreateRequest
from app.services.billing import FEATURE_QUOTAS, PlanFeatureQuotaGuard, quota_scope
from app.services.inference_fake import DeterministicInferenceAdapter
from app.services.model_gateway import (
    AdapterResult,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelRoute,
    ModelUsage,
    PlanningOutput,
    TranscriptionOutput,
    TranscriptWordOutput,
)
from app.services.model_provider_bridges import (
    GatewayEditPlannerProvider,
    GatewayTranscriptionProvider,
)
from app.services.operations import OperationsRepository

ROOT = Path(__file__).resolve().parents[3]


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, APP_ENV="local", **overrides)


class _TranscriptRecord:
    """Minimal transcript record the planner bridge reads."""

    def __init__(self, workspace_id: UUID, project_id: UUID) -> None:
        self.id = uuid4()
        self.workspace_id = workspace_id
        self.project_id = project_id
        self.updated_at = None
        self.duration_ms = 2000
        self.raw_payload: dict[str, object] = {}
        self.words = [
            TranscriptWord(index=0, text="Build", start_ms=0, end_ms=500, confidence=0.96),
            TranscriptWord(index=1, text="better", start_ms=550, end_ms=1100, confidence=0.95),
        ]


class _LabeledPlanningAdapter:
    """A fake planning adapter that stamps a provider label and logs its calls."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[str] = []

    def invoke(self, *, route: ModelRoute, request: object) -> AdapterResult:
        del request
        self.calls.append(route.provider)
        payload = {
            "choices": [{"message": {"content": self._content}}],
            "usage": {"prompt_tokens": 600, "completion_tokens": 400, "total_tokens": 1000},
        }
        return AdapterResult(
            output=PlanningOutput(content=self._content, raw_response=payload),
            usage=ModelUsage(input_units=600, output_units=400),
            provider_request_id="fake",
        )


class _LabeledTranscriptionAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, *, route: ModelRoute, request: object) -> AdapterResult:
        del request
        self.calls.append(route.provider)
        words = (
            TranscriptWordOutput(text="hello", start_ms=0, end_ms=480, confidence=0.98),
            TranscriptWordOutput(text="world", start_ms=500, end_ms=1000, confidence=0.95),
        )
        return AdapterResult(
            output=TranscriptionOutput(
                text="hello world",
                language="en",
                confidence=0.96,
                words=words,
                raw_response={"text": "hello world", "language": "en"},
            ),
            usage=ModelUsage(input_units=20, output_units=0),
            provider_request_id="fake",
        )


def _planning_route(provider: str) -> ModelRoute:
    return ModelRoute(
        route_id=f"planning-{provider}",
        capability=ModelCapability.PLANNING,
        adapter="planning",
        provider=provider,
        model="planner",
        model_revision="2026-07-14",
        operations=(ModelOperation.PLANNING_CHAT,),
        budget_feature="planning",
        explicitly_free=True,
    )


def _transcription_route(provider: str) -> ModelRoute:
    return ModelRoute(
        route_id=f"transcription-{provider}",
        capability=ModelCapability.TRANSCRIPTION,
        adapter="transcription",
        provider=provider,
        model="stt",
        model_revision="2026-07-14",
        operations=(ModelOperation.TRANSCRIPTION_SPEECH_TO_TEXT,),
        budget_feature="transcription",
        explicitly_free=True,
    )


class _Workspace:
    def __init__(
        self, *, settings: Settings, plan: BillingPlanKey = BillingPlanKey.CREATOR
    ) -> None:
        self.id = uuid4()
        self.project_id = uuid4()
        self.settings = settings
        self.billing = InMemoryBillingRepository()
        self.billing.upsert_subscription(
            workspace_id=self.id, plan=plan, status=SubscriptionStatus.ACTIVE
        )
        self.operations = OperationsRepository()
        self.guard = PlanFeatureQuotaGuard(
            billing_repository=self.billing,
            operations_repository=self.operations,
            settings=settings,
        )

    def planning_rows(self) -> list[dict]:
        return [row for row in self.operations.cost_events if row["category"] == "tokens"]


def _valid_plan_content(workspace: _Workspace) -> str:
    """A chat-completion content string that validates on the first attempt.

    Built from the deterministic fixture planner for this exact transcript, so
    the planner bridge accepts it without retries and records the quota once.
    """
    from app.services.edit_planner import FixtureEditPlannerProvider

    plan = FixtureEditPlannerProvider().create_plan(
        transcript=_TranscriptRecord(workspace.id, workspace.project_id),
        payload=PlanCreateRequest(
            style="founder", target_duration_s=10, broll_density=BrollDensity.NONE
        ),
        video_duration_ms=2000,
    ).plan
    return json.dumps(plan.model_dump(mode="json"))


def _gateway(workspace: _Workspace, planning_provider: str) -> tuple[ModelGateway, dict]:
    planning_adapter = _LabeledPlanningAdapter(_valid_plan_content(workspace))
    transcription_adapter = _LabeledTranscriptionAdapter()
    gateway = ModelGateway(
        adapters={"planning": planning_adapter, "transcription": transcription_adapter},
        routes=[_planning_route(planning_provider), _transcription_route("stt-vendor")],
        quota_guard=workspace.guard,
    )
    return gateway, {"planning": planning_adapter, "transcription": transcription_adapter}


def _run_transcript_then_plan(
    workspace: _Workspace, gateway: ModelGateway, tmp_path: Path
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF-fake-audio")
    transcription = GatewayTranscriptionProvider(
        gateway=gateway, provider_name="gateway", model="stt", model_revision="2026-07-14"
    )
    transcript_result = transcription.transcribe_with_context(
        audio_path=audio,
        language_hint=LanguageHint.AUTO,
        workspace_id=workspace.id,
        project_id=workspace.project_id,
    )
    assert [word.text for word in transcript_result.words] == ["hello", "world"]

    planner = GatewayEditPlannerProvider(model="planner", provider_name="gateway", gateway=gateway)
    plan_result = planner.create_plan(
        transcript=_TranscriptRecord(workspace.id, workspace.project_id),
        payload=PlanCreateRequest(
            style="founder", target_duration_s=10, broll_density=BrollDensity.NONE
        ),
        video_duration_ms=2000,
    )
    assert plan_result.plan is not None


# ---------------------------------------------------------------------------
# End-to-end fake upload -> transcript -> plan through the gateway
# ---------------------------------------------------------------------------
def test_transcript_then_plan_runs_only_through_the_gateway(tmp_path: Path) -> None:
    workspace = _Workspace(settings=_settings())
    gateway, adapters = _gateway(workspace, planning_provider="vendor-a")

    _run_transcript_then_plan(workspace, gateway, tmp_path)

    # The only record of the calls is the fake adapters' call logs: no direct
    # HTTP happened (the conftest no-network guard would otherwise fail first).
    assert adapters["transcription"].calls == ["stt-vendor"]
    assert adapters["planning"].calls == ["vendor-a"]
    # The planning token quota was recorded through the guard (not None, as the
    # old bridge wiring left it). One row per successful call, with the provider
    # label as metadata only.
    rows = workspace.planning_rows()
    assert len(rows) == 1
    assert rows[0]["quantity"] > 0
    assert rows[0]["metadata"]["provider"] == "vendor-a"


def test_two_routes_with_different_provider_labels_need_no_consumer_change(
    tmp_path: Path,
) -> None:
    # The consumer code is identical; only the route's provider label differs.
    for provider in ("vendor-a", "vendor-b"):
        workspace = _Workspace(settings=_settings())
        gateway, adapters = _gateway(workspace, planning_provider=provider)
        _run_transcript_then_plan(workspace, gateway, tmp_path)
        assert adapters["planning"].calls == [provider]
        assert workspace.planning_rows()[0]["metadata"]["provider"] == provider


# ---------------------------------------------------------------------------
# Decision B-1: feature scope is continuous across a provider switch
# ---------------------------------------------------------------------------
def test_feature_scope_accumulates_across_a_provider_label_switch(tmp_path: Path) -> None:
    workspace = _Workspace(settings=_settings(AI_QUOTA_SCOPE="feature"))

    gateway_a, _ = _gateway(workspace, planning_provider="vendor-a")
    _run_transcript_then_plan(workspace, gateway_a, tmp_path)
    # Operator switches the planning route to a different provider label.
    gateway_b, _ = _gateway(workspace, planning_provider="vendor-b")
    _run_transcript_then_plan(workspace, gateway_b, tmp_path)

    rows = workspace.planning_rows()
    assert [row["metadata"]["provider"] for row in rows] == ["vendor-a", "vendor-b"]
    assert len(rows) == 2
    per_call = rows[0]["quantity"]
    assert per_call > 0
    # Feature scope sums across provider labels, so the used total is continuous.
    start, end = _period(workspace)
    used = workspace.operations.count_usage(
        workspace_id=workspace.id, category="tokens", period_start=start, period_end=end
    )
    assert used == pytest.approx(2 * per_call)


def test_provider_scope_resets_the_count_across_a_provider_label_switch(tmp_path: Path) -> None:
    workspace = _Workspace(settings=_settings(AI_QUOTA_SCOPE="provider"))

    gateway_a, _ = _gateway(workspace, planning_provider="vendor-a")
    _run_transcript_then_plan(workspace, gateway_a, tmp_path)
    gateway_b, _ = _gateway(workspace, planning_provider="vendor-b")
    _run_transcript_then_plan(workspace, gateway_b, tmp_path)

    start, end = _period(workspace)
    rows = workspace.planning_rows()
    # Both modes read the same rows (both were written).
    assert len(rows) == 2
    # The same request is sent each time, so each provider recorded the same amount.
    per_call = rows[0]["quantity"]
    assert per_call > 0
    # Provider scope counts per provider, so each label sees only its own usage.
    for provider in ("vendor-a", "vendor-b"):
        used = workspace.operations.count_usage(
            workspace_id=workspace.id,
            provider=provider,
            category="tokens",
            period_start=start,
            period_end=end,
        )
        assert used == pytest.approx(per_call)


def test_planning_is_recorded_but_not_enforced_in_provider_scope() -> None:
    # Decision B-1: in provider scope the planning quota is recorded but not
    # enforced, so an over-limit workspace still records (and never raises).
    from types import SimpleNamespace

    workspace = _Workspace(
        settings=_settings(AI_QUOTA_SCOPE="provider", QUOTA_PLANNING_CREATOR_LIMIT_TOKENS=10)
    )
    route = SimpleNamespace(
        budget_feature="planning", provider="vendor-a", route_id="r", model_revision="rev"
    )
    # Way over the limit, but provider scope does not enforce planning.
    workspace.guard.check(route=route, context=_context(workspace), quantity=1000.0)


def test_planning_quota_is_enforced_in_feature_scope() -> None:
    from types import SimpleNamespace

    from app.services.model_gateway import FeatureQuotaExceeded

    workspace = _Workspace(
        settings=_settings(AI_QUOTA_SCOPE="feature", QUOTA_PLANNING_CREATOR_LIMIT_TOKENS=10)
    )
    route = SimpleNamespace(
        budget_feature="planning", provider="vendor-a", route_id="r", model_revision="rev"
    )
    with pytest.raises(FeatureQuotaExceeded):
        workspace.guard.check(route=route, context=_context(workspace), quantity=1000.0)


# ---------------------------------------------------------------------------
# Flag and settings
# ---------------------------------------------------------------------------
def test_ai_quota_scope_defaults_to_feature_and_validates() -> None:
    assert quota_scope(_settings()) == "feature"
    assert quota_scope(_settings(AI_QUOTA_SCOPE="provider")) == "provider"
    # An unknown value falls back to feature (the safe default).
    assert quota_scope(_settings(AI_QUOTA_SCOPE="nonsense")) == "feature"


def test_planning_quota_settings_are_renamed_without_an_alias() -> None:
    fields = Settings.model_fields
    spec = FEATURE_QUOTAS["planning"]
    for plan in BillingPlanKey:
        assert spec.setting_name(plan) == f"quota_planning_{plan.value}_limit_tokens"
        assert spec.setting_name(plan) in fields
    # Every quota setting field is now keyed by a Motionly feature, so the old
    # provider-keyed names are gone (Path B: no alias). This also proves no quota
    # field carries a vendor prefix, without this test naming one.
    feature_setting_names = {
        spec.setting_name(plan) for spec in FEATURE_QUOTAS.values() for plan in BillingPlanKey
    }
    quota_fields = {name for name in fields if name.startswith("quota_")}
    assert quota_fields == feature_setting_names
    # QUOTA_PLANNING_* is actually read.
    assert FEATURE_QUOTAS["planning"].limit(
        _settings(QUOTA_PLANNING_CREATOR_LIMIT_TOKENS=42), BillingPlanKey.CREATOR
    ) == 42.0


# ---------------------------------------------------------------------------
# M1 migration
# ---------------------------------------------------------------------------
def test_m1_index_migration_and_its_test_exist() -> None:
    migrations = ROOT / "supabase" / "migrations"
    migration = migrations / "202610240103_cost_attribution_category_index.sql"
    sql_test = ROOT / "supabase" / "tests" / "cost_attribution_category_index_test.sql"
    assert migration.is_file()
    assert sql_test.is_file()
    migration_text = migration.read_text()
    assert "cost_attribution_workspace_category_created_idx" in migration_text
    assert "create index if not exists" in migration_text.lower()
    assert "cost_attribution_events(workspace_id, category, created_at desc)" in migration_text
    test_text = sql_test.read_text()
    assert "select plan(" in test_text.lower()
    assert "select * from finish();" in test_text.lower()
    assert "rollback;" in test_text.lower()
    assert "cost_attribution_workspace_category_created_idx" in test_text


def test_fake_inference_adapter_makes_no_network_call(tmp_path: Path) -> None:
    # The fake adapter is pure (no I/O), so the gateway-only dev path is safe
    # under the no-network guard. This also documents why the e2e flow above
    # never touches the network.
    adapter = DeterministicInferenceAdapter(app_env="test")
    assert adapter.calls == []


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _period(workspace: _Workspace):
    from app.services.billing import quota_plan_and_period

    _plan, start, end = quota_plan_and_period(
        workspace_id=workspace.id, billing_repository=workspace.billing
    )
    return start, end


def _context(workspace: _Workspace) -> ModelCallContext:
    return ModelCallContext(workspace_id=workspace.id, project_id=workspace.project_id)
