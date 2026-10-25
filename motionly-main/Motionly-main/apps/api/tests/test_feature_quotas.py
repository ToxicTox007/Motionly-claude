"""Per-plan quotas for the remote AI features (Task 12).

The model gateway checks each metered call against the workspace plan's quota
before contacting the provider, and records usage once after each success.
Analysis features degrade with ``quota_exceeded``; stems fail visibly with 402.
"""

from __future__ import annotations

import json
import math
import re
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.errors import ApiError
from app.core.config import Settings, get_settings
from app.main import create_app
from app.repositories.billing import InMemoryBillingRepository
from app.schemas.billing import BillingPlanKey, SubscriptionStatus
from app.schemas.broll import BrollGenerateRequest, BrollSlotStatus
from app.schemas.plans import BrollRequest
from app.services import model_gateway as gateway_module
from app.services.audio_diarization import GatewayDiarizationProvider
from app.services.billing import FEATURE_QUOTAS, PlanFeatureQuotaGuard
from app.services.model_domain_adapters import LocalStemSeparationAdapter
from app.services.model_gateway import (
    BUDGET_FEATURES,
    AdapterResult,
    AudioRequest,
    DiarizationOutput,
    DiarizationRequest,
    DiarizationSegmentOutput,
    EmbeddingsRequest,
    FeatureQuotaCheckFailed,
    FeatureQuotaExceeded,
    ImageEmbeddingsRequest,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelProvidersExhausted,
    ModelRoute,
    ModelUsage,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    StemSeparationRequest,
    VisionRequest,
    feature_quota_units,
)
from app.services.operations import OperationsRepository
from app.services.scale import FixtureStemSeparator, _separate_stems
from tests.support.vendor_names import names_vendor as _names_vendor

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"
PLANS = tuple(BillingPlanKey)
# A neutral example object-detection revision (no vendor name).
VISION_REVISION = "objects-remote-builtin-stable-2026-10-22"
LOCAL_REVISION = "sha256:" + "a" * 64


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, APP_ENV="local", **overrides)


class Workspace:
    """A workspace on a plan, with in-memory billing and cost events."""

    def __init__(
        self,
        plan: BillingPlanKey = BillingPlanKey.FREE,
        *,
        settings: Settings | None = None,
        workspace_id: UUID | None = None,
        status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    ) -> None:
        self.id = workspace_id or uuid4()
        self.billing = InMemoryBillingRepository()
        self.billing.upsert_subscription(workspace_id=self.id, plan=plan, status=status)
        self.operations = OperationsRepository()
        self.settings = settings or _settings()
        self.guard = PlanFeatureQuotaGuard(
            billing_repository=self.billing,
            operations_repository=self.operations,
            settings=self.settings,
        )

    def context(self, **values) -> ModelCallContext:
        return ModelCallContext(workspace_id=self.id, **values)

    def use(self, feature: str, provider: str, quantity: float) -> None:
        spec = FEATURE_QUOTAS[feature]
        self.operations.record_cost(
            workspace_id=self.id,
            project_id=None,
            provider=provider,
            category=spec.category,
            quantity=quantity,
            unit=spec.unit,
            unit_cost_usd=0.0,
            metadata={"seed": uuid4().hex},
        )

    def rows(self, category: str) -> list[dict]:
        return [row for row in self.operations.cost_events if row["category"] == category]


@contextmanager
def _active_call(workspace: Workspace) -> Iterator[None]:
    """Run as if inside the job's gateway call (what nested providers inherit)."""
    token = gateway_module._ACTIVE_CALL_CONTEXT.set(  # noqa: SLF001 - test seam
        ModelCallContext(workspace_id=workspace.id, project_id=uuid4())
    )
    try:
        yield
    finally:
        gateway_module._ACTIVE_CALL_CONTEXT.reset(token)  # noqa: SLF001


def _wav(tmp_path: Path, seconds: float) -> Path:
    path = tmp_path / "speech.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x01\x00" * int(16_000 * seconds))
    return path


# -- neutral metered routes (plan Task 17: capability routes, no vendor code) --------
#
# The three vendor ``register_*`` helpers were deleted in Task 19 item 2. These
# local builders register equivalent *capability* routes with a neutral provider
# label and route id, keeping the quota behaviour (budget_feature, pricing,
# usage units) the vendor routes had.


def _register_objects_route(gateway, *, adapter, cost_per_image_usd: float) -> ModelRoute:
    route = ModelRoute(
        route_id="objects-remote",
        capability=ModelCapability.VISION,
        adapter="objects-remote-adapter",
        provider="remote",
        model="object-detection",
        model_revision=VISION_REVISION,
        operations=(ModelOperation.VISION_OBJECT_DETECTION,),
        priority=10,
        input_cost_per_million_usd=cost_per_image_usd * 1_000_000,
        requires_live_effects_gate=True,
        budget_feature="vision_objects",
    )
    gateway.register_adapter(route.adapter, adapter)
    gateway.register_route(route)
    return route


def _register_diarization_route(gateway, *, adapter, cost_per_hour_usd: float) -> ModelRoute:
    route = ModelRoute(
        route_id="diarization-remote",
        capability=ModelCapability.AUDIO,
        adapter="diarization-remote-adapter",
        provider="remote",
        model="diarization",
        model_revision="diarization-remote-v1",
        operations=(ModelOperation.AUDIO_DIARIZATION,),
        priority=10,
        input_cost_per_million_usd=cost_per_hour_usd / 3600 * 1_000_000,
        requires_live_effects_gate=True,
        budget_feature="audio_diarization",
        minimum_billable_units=20,
    )
    gateway.register_adapter(route.adapter, adapter)
    gateway.register_route(route)
    return route


def _register_stem_route(gateway, *, separator, cost_per_minute_usd: float) -> ModelRoute:
    route = ModelRoute(
        route_id="stems-remote",
        capability=ModelCapability.AUDIO,
        adapter="stems-remote-adapter",
        provider="remote",
        model=getattr(separator, "model", "stem-separation"),
        model_revision=getattr(separator, "model_revision", "stems-remote-v1"),
        operations=(ModelOperation.AUDIO_STEM_SEPARATION,),
        priority=10,
        input_cost_per_million_usd=cost_per_minute_usd / 60 * 1_000_000,
        requires_live_effects_gate=True,
        budget_feature="audio_stems",
    )
    gateway.register_adapter(route.adapter, LocalStemSeparationAdapter(separator))
    gateway.register_route(route)
    return route


# -- a metered objects route --------------------------------------------------------


class FakeObjects:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[int] = []
        self.error = error

    def invoke(self, *, route, request):  # noqa: ANN001
        del route
        self.calls.append(len(request.images))
        if self.error is not None:
            raise self.error
        return AdapterResult(
            output=ObjectDetectionOutput(detections=tuple(() for _ in request.images)),
            usage=ModelUsage(input_units=len(request.images)),
        )


def _objects_gateway(workspace: Workspace, adapter: FakeObjects | None = None):
    gateway = ModelGateway(adapters={}, routes=[], quota_guard=workspace.guard)
    adapter = adapter or FakeObjects()
    route = _register_objects_route(gateway, adapter=adapter, cost_per_image_usd=0.0015)
    return gateway, adapter, route


def _local_objects_route(gateway: ModelGateway, adapter: FakeObjects, **values) -> ModelRoute:
    route = ModelRoute(
        route_id="local-objects",
        capability=ModelCapability.VISION,
        adapter="local-objects",
        provider="local",
        model="yolo",
        model_revision=LOCAL_REVISION,
        operations=(ModelOperation.VISION_OBJECT_DETECTION,),
        **values,
    )
    gateway.register_adapter(route.adapter, adapter)
    gateway.register_route(route)
    return route


def _detect(gateway: ModelGateway, context: ModelCallContext, images: int):
    return gateway.invoke(
        ObjectDetectionRequest(images=tuple(b"jpeg" for _ in range(images))), context=context
    )


# -- per-plan boundaries -----------------------------------------------------------


@pytest.mark.parametrize("plan", PLANS)
def test_gateway_allows_each_plan_up_to_its_limit_and_refuses_beyond(plan) -> None:
    setting = FEATURE_QUOTAS["vision_objects"].setting_name(plan).upper()
    workspace = Workspace(plan, settings=_settings(**{setting: 4}))
    gateway, adapter, _route = _objects_gateway(workspace)

    _detect(gateway, workspace.context(), 3)
    _detect(gateway, workspace.context(), 1)  # exactly at the limit
    with pytest.raises(FeatureQuotaExceeded) as refused:
        _detect(gateway, workspace.context(), 1)

    assert refused.value.code == "quota_exceeded"
    assert refused.value.feature == "vision_objects"
    assert adapter.calls == [3, 1]  # the refused call never reached the provider
    rows = workspace.rows("vision_object_images")
    assert [row["quantity"] for row in rows] == [3.0, 1.0]
    assert {(row["provider"], row["unit"]) for row in rows} == {("remote", "images")}
    assert all(row["unit_cost_usd"] == pytest.approx(0.0015) for row in rows)
    assert all(row["metadata"]["route_id"] == "objects-remote" for row in rows)


@pytest.mark.parametrize("feature", sorted(FEATURE_QUOTAS))
@pytest.mark.parametrize("plan", PLANS)
def test_every_feature_default_has_an_at_limit_and_an_over_limit_boundary(plan, feature) -> None:
    spec = FEATURE_QUOTAS[feature]
    workspace = Workspace(plan)
    route = SimpleNamespace(
        budget_feature=feature, provider="vendor", route_id="r", model_revision="rev"
    )
    limit = spec.defaults[plan]
    assert spec.limit(workspace.settings, plan) == limit
    workspace.use(feature, "vendor", limit - 1)

    workspace.guard.check(route=route, context=workspace.context(), quantity=1)  # at limit
    with pytest.raises(FeatureQuotaExceeded):
        workspace.guard.check(route=route, context=workspace.context(), quantity=1.5)


def test_lapsed_paid_plans_get_free_limits() -> None:
    settings = _settings(
        QUOTA_EMBEDDINGS_FREE_LIMIT_INPUTS=2, QUOTA_EMBEDDINGS_PRO_LIMIT_INPUTS=100
    )
    workspace = Workspace(BillingPlanKey.PRO, settings=settings, status=SubscriptionStatus.CANCELED)
    route = SimpleNamespace(
        budget_feature="embeddings", provider="provider-a", route_id="r", model_revision="rev"
    )

    workspace.guard.check(route=route, context=workspace.context(), quantity=2)
    with pytest.raises(FeatureQuotaExceeded):
        workspace.guard.check(route=route, context=workspace.context(), quantity=3)


def test_usage_is_counted_per_vendor_and_feature() -> None:
    workspace = Workspace(settings=_settings(QUOTA_AUDIO_EMOTION_FREE_LIMIT_SECONDS=10))
    route = SimpleNamespace(
        budget_feature="audio_emotion", provider="provider-a", route_id="r", model_revision="rev"
    )
    workspace.use("audio_diarization", "provider-a", 100)  # other feature, never counted
    workspace.operations.record_cost(  # planning tokens on the same vendor, other category
        workspace_id=workspace.id,
        project_id=None,
        provider="provider-a",
        category="tokens",
        quantity=1_000_000,
        unit="token",
        unit_cost_usd=0.0,
    )

    # Only the audio_emotion category on this workspace counts; other features and
    # categories never do, in either scope.
    workspace.guard.check(route=route, context=workspace.context(), quantity=10)


def test_feature_scope_sums_usage_across_provider_labels() -> None:
    # Decision B-1 default: usage is summed per feature/category across every
    # provider label, so a different vendor's rows count toward the same quota.
    workspace = Workspace(
        settings=_settings(QUOTA_AUDIO_EMOTION_FREE_LIMIT_SECONDS=10, AI_QUOTA_SCOPE="feature")
    )
    route = SimpleNamespace(
        budget_feature="audio_emotion", provider="vendor-a", route_id="r", model_revision="rev"
    )
    workspace.use("audio_emotion", "vendor-b", 6)
    workspace.use("audio_emotion", "vendor-a", 2)

    # 6 + 2 = 8 used, so 2 fits (at the limit of 10) but 3 does not.
    workspace.guard.check(route=route, context=workspace.context(), quantity=2)
    with pytest.raises(FeatureQuotaExceeded):
        workspace.guard.check(route=route, context=workspace.context(), quantity=3)


def test_provider_scope_counts_only_the_routes_provider() -> None:
    # AI_QUOTA_SCOPE=provider restores pre-B-1 per-provider counting.
    workspace = Workspace(
        settings=_settings(QUOTA_AUDIO_EMOTION_FREE_LIMIT_SECONDS=10, AI_QUOTA_SCOPE="provider")
    )
    route = SimpleNamespace(
        budget_feature="audio_emotion", provider="vendor-a", route_id="r", model_revision="rev"
    )
    workspace.use("audio_emotion", "vendor-b", 100)  # another provider does not count
    workspace.use("audio_emotion", "vendor-a", 8)

    workspace.guard.check(route=route, context=workspace.context(), quantity=2)
    with pytest.raises(FeatureQuotaExceeded):
        workspace.guard.check(route=route, context=workspace.context(), quantity=3)


# -- metering ------------------------------------------------------------------------


def test_failed_provider_calls_record_no_usage() -> None:
    workspace = Workspace()
    gateway, adapter, _route = _objects_gateway(workspace, FakeObjects(RuntimeError("boom")))

    with pytest.raises(ModelProvidersExhausted):
        _detect(gateway, workspace.context(), 2)

    assert adapter.calls == [2]
    assert workspace.operations.cost_events == []


class _NoBilling:
    def get_subscription(self, **_values):
        raise AssertionError("the quota must not be looked up")


def test_local_routes_and_unattributed_calls_are_never_metered() -> None:
    operations = OperationsRepository()
    guard = PlanFeatureQuotaGuard(
        billing_repository=_NoBilling(), operations_repository=operations, settings=_settings()
    )
    gateway = ModelGateway(adapters={}, routes=[], quota_guard=guard)
    remote = FakeObjects()
    route = _register_objects_route(gateway, adapter=remote, cost_per_image_usd=0.0015)
    local = FakeObjects()
    _local_objects_route(gateway, local)

    _detect(gateway, ModelCallContext(workspace_id=uuid4(), required_route_id="local-objects"), 2)
    _detect(gateway, ModelCallContext(required_route_id=route.route_id), 2)

    assert local.calls == [2] and remote.calls == [2]
    assert operations.cost_events == []


class _DownBilling:
    def get_subscription(self, **_values):
        raise httpx.ConnectError("billing is down")


def test_a_failed_quota_lookup_refuses_the_paid_call() -> None:
    workspace = Workspace()
    workspace.guard.billing_repository = _DownBilling()
    gateway, adapter, route = _objects_gateway(workspace)

    with pytest.raises(FeatureQuotaCheckFailed) as refused:
        _detect(gateway, workspace.context(), 1)
    with pytest.raises(FeatureQuotaCheckFailed):
        gateway.check_feature_quota(
            route_id=route.route_id, context=workspace.context(), quantity=1
        )

    assert refused.value.code == "quota_check_failed"
    assert adapter.calls == []


class _ReadOnlyOperations(OperationsRepository):
    def record_cost(self, **_values) -> None:
        raise httpx.ConnectError("database is down")


def test_a_failed_usage_write_does_not_fail_the_paid_call() -> None:
    workspace = Workspace()
    workspace.guard.operations_repository = _ReadOnlyOperations()
    gateway, adapter, _route = _objects_gateway(workspace)

    result = _detect(gateway, workspace.context(), 2)

    assert isinstance(result.output, ObjectDetectionOutput)
    assert adapter.calls == [2]


def test_a_refused_metered_route_falls_back_to_an_unmetered_one() -> None:
    workspace = Workspace(settings=_settings(QUOTA_VISION_OBJECTS_FREE_LIMIT_IMAGES=0))
    gateway, remote, _route = _objects_gateway(workspace)
    local = FakeObjects()
    _local_objects_route(gateway, local, priority=500)

    result = _detect(gateway, workspace.context(), 2)

    assert remote.calls == [] and local.calls == [2]
    assert result.metadata.route_id == "local-objects"
    assert result.metadata.fallback_count == 1
    assert workspace.operations.cost_events == []


def test_up_front_check_refuses_a_job_that_would_not_fit() -> None:
    workspace = Workspace(settings=_settings(QUOTA_VISION_OBJECTS_FREE_LIMIT_IMAGES=10))
    gateway, adapter, route = _objects_gateway(workspace)

    gateway.check_feature_quota(route_id=route.route_id, context=workspace.context(), quantity=10)
    with pytest.raises(FeatureQuotaExceeded):
        gateway.check_feature_quota(
            route_id=route.route_id, context=workspace.context(), quantity=11
        )
    # Unknown routes and gateways without a guard are never refused.
    gateway.check_feature_quota(route_id="missing", context=workspace.context(), quantity=1e9)
    ModelGateway(adapters={}, routes=[]).check_feature_quota(
        route_id=route.route_id, context=workspace.context(), quantity=1e9
    )
    assert adapter.calls == []
    assert workspace.operations.cost_events == []


def test_quota_units_are_images_inputs_or_audio_seconds(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    assert feature_quota_units(ObjectDetectionRequest(images=(b"a", b"b"))) == 2
    assert feature_quota_units(ImageEmbeddingsRequest(images=(b"a",))) == 1
    assert feature_quota_units(VisionRequest(prompt="p", image_urls=("u1", "u2", "u3"))) == 3
    assert feature_quota_units(EmbeddingsRequest(inputs=("a", "b"))) == 2
    assert feature_quota_units(DiarizationRequest(audio_path=audio, duration_s=12.5)) == 12.5
    stems = StemSeparationRequest(source_path=audio, model="m", duration_s=30)
    assert feature_quota_units(stems) == 30
    assert feature_quota_units(AudioRequest(prompt="p", audio_path=audio, duration_s=4)) == 4
    assert feature_quota_units(AudioRequest(prompt="p", audio_path=audio)) == 0
    with pytest.raises(ValueError):
        AudioRequest(prompt="p", audio_path=audio, duration_s=-1)


# -- settings and schema contracts ---------------------------------------------------


def test_planning_and_video_generation_quotas_are_feature_keyed() -> None:
    # Decision B-1: the old provider-keyed video-generation and planning quotas
    # are now feature-keyed ``video_generation``/``planning`` with QUOTA_*
    # settings renamed accordingly (Path B: no alias for the old names).
    fields = Settings.model_fields
    expected = {
        "video_generation": ("seconds", "generated_seconds", (0.0, 60.0, 300.0, 1200.0)),
        "planning": ("tokens", "tokens", (100_000.0, 500_000.0, 2_000_000.0, 10_000_000.0)),
    }
    for name, (unit, category, defaults) in expected.items():
        spec = FEATURE_QUOTAS[name]
        assert (spec.unit, spec.category) == (unit, category)
        for plan, default in zip(PLANS, defaults, strict=True):
            setting = f"quota_{name}_{plan.value}_limit_{unit}"
            assert spec.setting_name(plan) == setting
            assert spec.defaults[plan] == default == fields[setting].default
    # The old provider-keyed setting names are gone (Path B, no alias): no quota
    # setting is named after a provider, only after a Motionly feature.
    vendor_named = [name for name in fields if name.startswith("quota_") and _names_vendor(name)]
    assert vendor_named == []


def test_feature_quota_settings_mirror_the_table() -> None:
    fields = Settings.model_fields
    for spec in FEATURE_QUOTAS.values():
        for plan in PLANS:
            field = fields[spec.setting_name(plan)]
            # video_generation's Free default is 0 (no plan generation on Free);
            # every other default is positive. All are non-negative.
            assert field.default == spec.defaults[plan] >= 0
            assert field.alias == spec.setting_name(plan).upper()
        # A higher plan never gets less.
        values = [spec.defaults[plan] for plan in PLANS]
        assert values == sorted(values)


def test_quota_limits_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_EMBEDDINGS_PRO_LIMIT_INPUTS", "42")

    assert FEATURE_QUOTAS["embeddings"].limit(_settings(), BillingPlanKey.PRO) == 42.0
    with pytest.raises(ValueError):
        _settings(QUOTA_AUDIO_STEMS_FREE_LIMIT_SECONDS=-1)


def test_quota_features_are_budget_features() -> None:
    assert set(FEATURE_QUOTAS) <= BUDGET_FEATURES
    # Decision B-1: planning and video_generation are now feature-keyed quotas.
    # Transcription is never plan-metered.
    assert {"planning", "video_generation"} <= set(FEATURE_QUOTAS)
    assert "transcription" not in FEATURE_QUOTAS
    categories = [spec.category for spec in FEATURE_QUOTAS.values()]
    assert len(set(categories)) == len(categories)


def _allowed_cost_categories() -> set[str]:
    """The cost_attribution_events.category values allowed by the latest migration."""
    clause = re.compile(r"category\s+in\s*\(([^)]*)\)", re.IGNORECASE)
    allowed: set[str] = set()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text()
        if "cost_attribution_events" not in text:
            continue
        for match in clause.finditer(text):
            if "generated_seconds" in match.group(1):
                allowed = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    return allowed


def test_every_quota_category_is_allowed_by_the_database() -> None:
    allowed = _allowed_cost_categories()
    specs = tuple(FEATURE_QUOTAS.values())

    assert {spec.category for spec in specs} <= allowed
    # The original categories are kept.
    assert {"tokens", "generated_seconds", "compute_cpu", "compute_gpu", "storage", "egress"} <= (
        allowed
    )


def test_default_settings_meter_the_feature_keyed_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    get_settings.cache_clear()

    app = create_app()

    gateway = app.state.model_gateway
    assert isinstance(gateway.quota_guard, PlanFeatureQuotaGuard)
    assert gateway.quota_guard.billing_repository is app.state.billing_repository
    # Decision B-1: the dev fake planning route is feature-keyed and metered;
    # the dev transcription route is not plan-metered.
    metered = [route.route_id for route in gateway.routes if route.budget_feature in FEATURE_QUOTAS]
    assert "dev-planning-fake" in metered
    assert all("transcription" not in route_id for route_id in metered)
    get_settings.cache_clear()


# -- analysis features degrade -------------------------------------------------------




class FakeDiarization:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def invoke(self, *, route, request):  # noqa: ANN001
        del route
        self.calls.append(request.duration_s)
        return AdapterResult(
            output=DiarizationOutput(
                segments=(DiarizationSegmentOutput(speaker="A", start_ms=0, end_ms=1000),),
                raw_response={},
            ),
            usage=ModelUsage(input_units=math.ceil(request.duration_s)),
        )


@pytest.mark.parametrize(("limit", "refused"), [(29.0, True), (30.0, False)])
def test_diarization_over_quota_keeps_the_transcript_estimate(
    tmp_path: Path, limit: float, refused: bool
) -> None:
    workspace = Workspace(settings=_settings(QUOTA_AUDIO_DIARIZATION_FREE_LIMIT_SECONDS=limit))
    gateway = ModelGateway(adapters={}, routes=[], quota_guard=workspace.guard)
    adapter = FakeDiarization()
    route = _register_diarization_route(gateway, adapter=adapter, cost_per_hour_usd=0.3)
    provider = GatewayDiarizationProvider(
        gateway=gateway, model=route.model, model_revision=route.model_revision
    )

    with _active_call(workspace):
        result = provider.diarize(audio_path=_wav(tmp_path, 30))

    rows = workspace.rows("diarization_seconds")
    if refused:
        assert result["status"] == "unavailable:quota_exceeded"
        assert result["degraded"] is True and result["heuristic_fallback"] is True
        assert adapter.calls == [] and rows == []
    else:
        assert result["speaker_count"] == 1 and "degraded" not in result
        assert adapter.calls == [30.0]
        assert [(row["provider"], row["quantity"]) for row in rows] == [("remote", 30.0)]


def test_emotion_is_refused_before_any_chunk_when_the_file_would_not_fit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_remote_audio_emotion import _gateway as emotion_gateway
    from tests.test_remote_audio_emotion import _install as install_emotion_route
    from tests.test_remote_audio_emotion import _provider as emotion_provider

    calls = install_emotion_route(monkeypatch, [])
    workspace = Workspace(settings=_settings(QUOTA_AUDIO_EMOTION_FREE_LIMIT_SECONDS=25))
    provider = emotion_provider(emotion_gateway(quota_guard=workspace.guard))  # 10 s chunks

    with _active_call(workspace):
        result = provider.classify_windows_detailed(audio_path=_wav(tmp_path, 30))

    assert result["degraded"] is True
    assert result["status"] == "unavailable:quota_exceeded"
    # Per-chunk checks alone would have paid for two chunks before refusing.
    assert calls == []
    assert workspace.rows("emotion_seconds") == []


def test_emotion_records_each_chunk_once_and_never_sends_the_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_remote_audio_emotion import _completion, _window
    from tests.test_remote_audio_emotion import _gateway as emotion_gateway
    from tests.test_remote_audio_emotion import _install as install_emotion_route
    from tests.test_remote_audio_emotion import _provider as emotion_provider

    reply = _completion({"windows": [_window(0), _window(1)]})
    calls = install_emotion_route(monkeypatch, [(200, reply)] * 3)
    workspace = Workspace(settings=_settings(QUOTA_AUDIO_EMOTION_FREE_LIMIT_SECONDS=30))
    provider = emotion_provider(emotion_gateway(quota_guard=workspace.guard))

    with _active_call(workspace):
        result = provider.classify_windows_detailed(audio_path=_wav(tmp_path, 30))

    assert "degraded" not in result and len(result["windows"]) == 6
    rows = workspace.rows("emotion_seconds")
    assert [(row["provider"], row["quantity"]) for row in rows] == [("remote", 10.0)] * 3
    assert len(calls) == 3
    assert all("duration_s" not in json.dumps(call["json"]) for call in calls)


def _run_broll(monkeypatch: pytest.MonkeyPatch, embed_error: Exception) -> tuple[dict, object]:
    import app.services.layer_jobs as layer_jobs

    request = BrollRequest(start_ms=500, end_ms=2000, keywords=["desk"], priority=0.8)
    monkeypatch.setattr(layer_jobs, "latest_visual_manifest_for_broll", lambda **_k: None)
    monkeypatch.setattr(layer_jobs, "desired_requests", lambda plan, density: [request])
    monkeypatch.setattr(
        layer_jobs,
        "apply_stage4_broll_rules",
        lambda *, requests, visual_manifest, duration_ms: requests,
    )

    class Embeddings:
        gateway = object()  # embed through the gateway signature (workspace attribution)
        preferred_model_revision = "embeddings-v2-2024-11-05"
        model_version = "embeddings-v2-2024-11-05"
        cache_version = "gateway:test"

        def __init__(self) -> None:
            self.calls = 0

        def embed_text(self, text, *, workspace_id=None, project_id=None):  # noqa: ANN001
            del text, workspace_id, project_id
            self.calls += 1
            raise embed_error

    created: dict = {}

    def create_slot_set(**values):
        created.update(values)
        return [SimpleNamespace(id=uuid4(), status=BrollSlotStatus.FAILED) for _ in values["slots"]]

    plan = SimpleNamespace(
        id=uuid4(),
        plan=SimpleNamespace(target_duration_s=4),
        style=SimpleNamespace(value="creator_influencer"),
        broll_density=SimpleNamespace(value="medium"),
    )
    repository = SimpleNamespace(
        active_plan=lambda **_k: plan,
        find_cached_slots=lambda **_k: [],
        create_slot_set=create_slot_set,
    )
    embeddings = Embeddings()
    layer_jobs.execute_broll_generation(
        repository=repository,
        billing_repository=None,
        providers=[SimpleNamespace(provider_key="pexels", search=lambda **_k: [])],
        storage_signer=None,
        stock_asset_enrichment_queue=None,
        settings=SimpleNamespace(billing_enabled=False),
        embedding_provider=embeddings,
        workspace_id=uuid4(),
        project_id=uuid4(),
        user_id=None,
        payload=BrollGenerateRequest(),
    )
    return created, embeddings


def test_broll_ranks_lexically_when_the_embeddings_quota_is_used_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, embeddings = _run_broll(
        monkeypatch, FeatureQuotaExceeded("used up", feature="embeddings")
    )

    metadata = created["slots"][0]["metadata"]
    assert embeddings.calls == 1
    assert metadata["embedding_unavailable"] == "quota_exceeded"
    assert metadata["embedding_fallback_used"] is True  # kept out of the slot cache
    assert metadata["ranking_mode"] == "lexical_fallback"


def test_broll_provider_failures_still_fail_the_job_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = ModelProvidersExhausted(ModelCapability.EMBEDDINGS, [{"code": "provider_timeout"}])

    with pytest.raises(ModelProvidersExhausted):
        _run_broll(monkeypatch, failure)


# -- stems fail visibly --------------------------------------------------------------


def _stems(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, limit: float):
    from tests.test_scale import _stem_context

    uploads, record, settings = _stem_context(tmp_path)
    # The source comes from storage; materialize it to the work dir like the
    # production stem path does (plan Task 17).
    monkeypatch.setattr(
        "app.services.scale.materialize_upload_source",
        lambda *, upload, storage: storage.ensure_object(
            bucket=upload.storage_bucket, path=upload.storage_path
        ),
    )
    monkeypatch.setattr("app.services.scale.probe_duration_seconds", lambda _path: 90.0)
    workspace = Workspace(
        settings=_settings(QUOTA_AUDIO_STEMS_FREE_LIMIT_SECONDS=limit),
        workspace_id=record.workspace_id,
    )
    gateway = ModelGateway(adapters={}, routes=[], quota_guard=workspace.guard)
    separator = FixtureStemSeparator()
    route = _register_stem_route(gateway, separator=separator, cost_per_minute_usd=0.6)

    def run():
        return _separate_stems(
            record=record,
            settings=settings,
            upload_repository=uploads,
            stem_separator=separator,
            model_gateway=gateway,
            stem_gateway_route=route,
        )

    return run, workspace


def test_stems_over_quota_fail_visibly_with_402(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run, workspace = _stems(monkeypatch, tmp_path, limit=60)

    with pytest.raises(ApiError) as refused:
        run()

    assert refused.value.status_code == 402
    assert refused.value.code == "audio_stems_quota_exceeded"
    assert workspace.rows("stem_seconds") == []  # nothing sent to the provider


def test_stems_within_quota_are_recorded_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run, workspace = _stems(monkeypatch, tmp_path, limit=90)

    result = run()

    assert result["model_gateway"]["route_id"] == "stems-remote"
    rows = workspace.rows("stem_seconds")
    assert [(row["provider"], row["quantity"], row["unit"]) for row in rows] == [
        ("remote", 90.0, "seconds")
    ]
    assert rows[0]["unit_cost_usd"] == pytest.approx(0.01)  # 0.6 per minute
