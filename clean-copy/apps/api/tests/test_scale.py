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
    update_collaboration_manifest,
)
from app.repositories.analytics import InMemoryAnalyticsRepository
from app.repositories.scale import InMemoryScaleRepository
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
