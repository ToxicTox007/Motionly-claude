import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.plans import _apply_phase5_learning
from app.api.scale import (
    get_collaboration_manifest,
    list_project_scale_resources,
    list_saved_templates,
    update_collaboration_manifest,
)
from app.repositories.analytics import InMemoryAnalyticsRepository
from app.repositories.scale import (
    SCALE_RESOURCE_COLUMNS,
    InMemoryScaleRepository,
    SupabaseScaleRepository,
)
from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer
from app.schemas.plans import PlanCreateRequest
from app.schemas.scale import CollaborationManifestUpdateRequest, ScaleResourceStatus
from app.services.queue import QueueRegistry, ScaleQueue
from app.services.scale import (
    FixtureRunwayProvider,
    _remove_stem_temp_directory,
    execute_scale_resource,
)


def test_scale_repository_and_queue_persist_phase5_jobs() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    repository = InMemoryScaleRepository()
    resource = repository.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="stem_separation",
        name="Stems",
        status=ScaleResourceStatus.QUEUED,
        payload={"model": "htdemucs"},
    )
    registry = QueueRegistry()
    queued = ScaleQueue(registry).enqueue_resource(
        resource_id=resource.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )

    assert queued.queue_name == "intelligence"
    assert queued.kind == "phase5"
    assert (
        repository.get(resource_id=resource.id, workspace_id=workspace_id).payload["model"]
        == "htdemucs"
    )


def test_supabase_scale_repository_lists_with_explicit_projection(monkeypatch) -> None:
    repository = SupabaseScaleRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: [])

    monkeypatch.setattr("app.repositories.scale.SupabaseRestClient.request", fake_request)

    assert (
        repository.list(
            workspace_id=workspace_id,
            project_id=project_id,
            kind="collaboration_manifest",
            limit=25,
        )
        == []
    )
    assert calls == [
        (
            "GET",
            "/phase5_resources",
            {
                "params": {
                    "workspace_id": f"eq.{workspace_id}",
                    "select": SCALE_RESOURCE_COLUMNS,
                    "order": "updated_at.desc",
                    "limit": "25",
                    "kind": "eq.collaboration_manifest",
                    "project_id": f"eq.{project_id}",
                }
            },
        )
    ]


def test_supabase_scale_async_reads_use_bounded_projections(monkeypatch) -> None:
    repository = SupabaseScaleRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    resource_id = uuid4()
    now = "2026-07-06T00:00:00+00:00"
    row = {
        "id": str(resource_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "kind": "collaboration_manifest",
        "name": "Shared edit manifest",
        "status": "ready",
        "payload": {"manifest": {}},
        "result": {},
        "revision": 1,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    responses = [[], [row]]
    calls = []

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: responses.pop(0))

    monkeypatch.setattr(
        "app.repositories.scale.SupabaseRestClient.async_request",
        fake_async_request,
    )

    listed = asyncio.run(
        repository.list_async(
            workspace_id=workspace_id,
            project_id=project_id,
            kind="collaboration_manifest",
            limit=25,
        )
    )
    record = asyncio.run(
        repository.get_async(resource_id=resource_id, workspace_id=workspace_id)
    )

    assert listed == []
    assert record.id == resource_id
    assert calls == [
        (
            "GET",
            "/phase5_resources",
            {
                "params": {
                    "workspace_id": f"eq.{workspace_id}",
                    "select": SCALE_RESOURCE_COLUMNS,
                    "order": "updated_at.desc",
                    "limit": "25",
                    "kind": "eq.collaboration_manifest",
                    "project_id": f"eq.{project_id}",
                }
            },
        ),
        (
            "GET",
            "/phase5_resources",
            {
                "params": {
                    "id": f"eq.{resource_id}",
                    "workspace_id": f"eq.{workspace_id}",
                    "select": SCALE_RESOURCE_COLUMNS,
                    "limit": "1",
                }
            },
        ),
    ]


def test_scale_api_prefers_async_repository_methods() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    actor = Actor(user_id=uuid4(), workspace_id=workspace_id)

    class AsyncScaleRepository(InMemoryScaleRepository):
        def __init__(self) -> None:
            super().__init__()
            self.async_calls: list[str] = []

        async def create_async(self, **kwargs):
            self.async_calls.append("create")
            return self.create(**kwargs)

        async def list_async(self, **kwargs):
            self.async_calls.append("list")
            return self.list(**kwargs)

        async def latest_async(self, **kwargs):
            self.async_calls.append("latest")
            return self.latest(**kwargs)

        async def update_if_revision_async(self, **kwargs):
            self.async_calls.append("update-if-revision")
            return self.update_if_revision(**kwargs)

    repository = AsyncScaleRepository()
    repository.create(
        workspace_id=workspace_id,
        project_id=None,
        kind="saved_template",
        name="Template",
        status=ScaleResourceStatus.READY,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                scale_repository=repository,
                project_repository=SimpleNamespace(get_project=lambda **_: object()),
            )
        )
    )

    asyncio.run(
        list_project_scale_resources(
            project_id=project_id,
            actor=actor,
            request=request,
            limit=25,
        )
    )
    asyncio.run(list_saved_templates(actor=actor, request=request, limit=25))
    initial = asyncio.run(
        get_collaboration_manifest(project_id=project_id, actor=actor, request=request)
    )
    asyncio.run(
        update_collaboration_manifest(
            project_id=project_id,
            payload=CollaborationManifestUpdateRequest(
                base_revision=initial.revision,
                manifest={"plan_id": "one"},
            ),
            actor=actor,
            request=request,
        )
    )

    assert repository.async_calls == [
        "list",
        "list",
        "latest",
        "create",
        "latest",
        "update-if-revision",
    ]


def test_list_project_scale_resources_passes_limit_to_repository() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    repository = InMemoryScaleRepository()
    first = repository.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="model_training",
        name="First",
        status=ScaleResourceStatus.READY,
    )
    second = repository.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="model_training",
        name="Second",
        status=ScaleResourceStatus.READY,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                project_repository=SimpleNamespace(get_project=lambda **_kwargs: None),
                scale_repository=repository,
            )
        )
    )

    response = asyncio.run(
        list_project_scale_resources(
            project_id=project_id,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            request=request,
            limit=1,
        )
    )

    assert [resource.id for resource in response.resources] == [second.id]
    assert first.id != second.id


def test_list_saved_templates_passes_limit_to_repository() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    repository = InMemoryScaleRepository()
    first = repository.create(
        workspace_id=workspace_id,
        project_id=None,
        kind="saved_template",
        name="First",
        status=ScaleResourceStatus.READY,
    )
    second = repository.create(
        workspace_id=workspace_id,
        project_id=None,
        kind="saved_template",
        name="Second",
        status=ScaleResourceStatus.READY,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(scale_repository=repository))
    )

    response = asyncio.run(
        list_saved_templates(
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            request=request,
            limit=1,
        )
    )

    assert [resource.id for resource in response.resources] == [second.id]
    assert first.id != second.id


def test_model_training_turns_override_events_into_applied_preferences() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    analytics = InMemoryAnalyticsRepository()
    analytics.record_event(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        event_type=AIEditFeedbackEventType.CHANGED,
        layer=AIEditLayer.CUTS,
        source="manual_editor",
        ai_payload={"pace": "slow"},
        user_payload={"pace": "fast"},
    )
    scale = InMemoryScaleRepository()
    training = scale.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="model_training",
        name="Preference adapter",
        status=ScaleResourceStatus.QUEUED,
        payload={"minimum_examples": 1},
    )

    completed = execute_scale_resource(
        repository=scale,
        resource_id=training.id,
        workspace_id=workspace_id,
        settings=None,
        upload_repository=None,
        broll_repository=None,
        visual_provider=None,
        analytics_repository=analytics,
        stem_separator=None,
        runway_provider=None,
    )
    adapted = _apply_phase5_learning(
        payload=PlanCreateRequest(),
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scale_repository=scale))),
        workspace_id=workspace_id,
        project_id=project_id,
    )

    assert completed.status == ScaleResourceStatus.READY
    assert completed.result["preferences"]["cuts"] == {"pace": "fast"}
    assert "Project learned preferences" in adapted.prompt


def test_collaboration_manifest_rejects_stale_revision() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    actor = Actor(user_id=uuid4(), workspace_id=workspace_id)
    repository = InMemoryScaleRepository()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                scale_repository=repository,
                project_repository=SimpleNamespace(get_project=lambda **_: object()),
            )
        )
    )
    initial = asyncio.run(
        get_collaboration_manifest(project_id=project_id, actor=actor, request=request)
    )
    updated = asyncio.run(
        update_collaboration_manifest(
            project_id=project_id,
            payload=CollaborationManifestUpdateRequest(
                base_revision=initial.revision, manifest={"plan_id": "one"}
            ),
            actor=actor,
            request=request,
        )
    )
    assert updated.revision == 1

    with pytest.raises(ApiError) as error:
        asyncio.run(
            update_collaboration_manifest(
                project_id=project_id,
                payload=CollaborationManifestUpdateRequest(
                    base_revision=0, manifest={"plan_id": "stale"}
                ),
                actor=actor,
                request=request,
            )
        )
    assert error.value.code == "collaboration_revision_conflict"


def test_fixture_runway_provider_returns_renderable_asset_url() -> None:
    result = FixtureRunwayProvider().generate(
        prompt="cinematic city sunrise",
        prompt_image_url=None,
        duration_s=5,
        ratio="768:1280",
    )
    assert result.task_id.startswith("fixture-")
    assert result.video_url.startswith("fixture://runway/")


def test_stem_cleanup_only_removes_motionly_temp_directory(tmp_path) -> None:
    safe_parent = tmp_path / "do-not-remove"
    safe_parent.mkdir()
    unrelated = safe_parent / "vocals.wav"
    unrelated.write_bytes(b"audio")

    _remove_stem_temp_directory({"vocals": unrelated})

    assert safe_parent.exists()

    generated = Path(tempfile.mkdtemp(prefix="motionly-fixture-stems-"))
    stem = generated / "vocals.wav"
    stem.write_bytes(b"audio")
    _remove_stem_temp_directory({"vocals": stem})
    assert not generated.exists()


def test_custom_style_and_saved_template_change_future_plan_requests() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    scale = InMemoryScaleRepository()
    template = scale.create(
        workspace_id=workspace_id,
        project_id=None,
        kind="saved_template",
        name="Fast social",
        status=ScaleResourceStatus.READY,
        payload={
            "settings": {
                "style": "fast_paced",
                "target_duration_s": 20,
                "broll_density": "high",
                "direction": "Use hard cuts",
            }
        },
    )
    profile = scale.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="style_profile",
        name="House style",
        status=ScaleResourceStatus.READY,
        payload={"references": [{"status": "ready"}] * 5},
        result={
            "learned_settings": {
                "style": "cinematic",
                "target_duration_s": 28,
                "broll_density": "medium",
                "caption_direction": "quiet editorial captions",
            }
        },
    )
    adapted = _apply_phase5_learning(
        payload=PlanCreateRequest(
            saved_template_id=template.id,
            style_profile_id=profile.id,
        ),
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scale_repository=scale))),
        workspace_id=workspace_id,
        project_id=project_id,
    )

    assert adapted.style.value == "cinematic"
    assert adapted.target_duration_s == 28
    assert "hard cuts" in adapted.prompt.lower()
    assert "quiet editorial captions" in adapted.prompt
