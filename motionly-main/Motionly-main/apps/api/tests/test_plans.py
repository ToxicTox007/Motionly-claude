import asyncio
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.exceptions import RequestValidationError

from app.api.dependencies import Actor
from app.api.errors import ApiError, request_validation_error_handler
from app.api.plans import (
    activate_plan,
    compile_project_timeline,
    create_plan as _create_plan_api,
    create_plan_snapshot,
    list_plan_snapshots,
    list_plans,
    restore_plan_snapshot,
    update_plan_cuts,
    update_plan_manual_elements,
    update_project_timeline,
    get_plan_job_status,
)
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.repositories.visual import InMemoryVisualRepository
from app.schemas.captions import CaptionPresetKey, LanguageHint
from app.schemas.plans import (
    BrollDensity,
    EditPlanStatus,
    EditPlanStyle,
    ManualElement,
    PlanCreateRequest,
    PlanCutsUpdateRequest,
    PlanManualElementsUpdateRequest,
    PlanSnapshotCreateRequest,
    StyleClassification,
    TimelineUpdateRequest,
)
from app.schemas.uploads import UploadStatus
from app.schemas.visual import (
    BoundingBox,
    CameraMotion,
    DominantColor,
    ObjectDetection,
    SceneSegment,
    SceneType,
    VideoManifest,
    VisualManifestStatus,
)
from app.services.caption_chunking import chunk_caption_words
from app.services.caption_presets import CAPTION_SCHEMA_VERSION, get_caption_preset
from app.services.edit_planner import FixtureEditPlannerProvider, HttpEditPlannerProvider
from app.services.queue import PlanQueue
from app.services.transcription_provider import FixtureTranscriptionProvider


async def create_plan(
    project_id: UUID,
    payload: PlanCreateRequest,
    actor: Actor,
    repository: InMemoryPlanRepository,
    planner_provider,
    plan_queue,
    request=None,
):
    if request is None:
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    settings=SimpleNamespace(
                        media_work_dir=".motionly/test-storage",
                        transcription_debug_retain_audio=False,
                    ),
                    plan_repository=repository,
                    edit_planner_provider=planner_provider,
                    operations_repository=None,
                    queue_registry=plan_queue.registry,
                )
            )
        )
    
    response = await _create_plan_api(
        project_id=project_id,
        payload=payload,
        actor=actor,
        repository=repository,
        planner_provider=planner_provider,
        plan_queue=plan_queue,
        response=None,
        request=request,
    )
    
    if response.status == "ready":
        return response
        
    job_id = response.job_id
    job = plan_queue.registry.jobs[job_id]
    
    from app.api.internal_jobs import _execute_job
    result = _execute_job(job=job, request=request)
    
    plan_id = UUID(result["plan_id"])
    record = repository.get_plan(project_id=project_id, workspace_id=actor.workspace_id, plan_id=plan_id)
    
    return response.model_copy(
        update={
            "plan": record.to_response(),
            "status": "ready"
        }
    )



def make_plan_repository(
    *,
    with_transcript: bool = True,
) -> tuple[InMemoryPlanRepository, UUID, UUID, UUID]:
    upload_repository = InMemoryUploadRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = upload_repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=1000,
    )
    upload.status = UploadStatus.READY
    upload.metadata.duration_ms = 12_000
    upload.metadata.has_audio = True
    upload_repository.attach_active_video(project_id=project_id, video_id=upload.id)
    caption_repository = InMemoryCaptionRepository(upload_repository)
    if with_transcript:
        caption_repository.create_transcript(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=upload.id,
            status="ready",
            language="en",
            language_hint="auto",
            provider="fixture",
            model="whisper-1",
            confidence=0.95,
            duration_ms=12_000,
            raw_payload={},
            words=FixtureTranscriptionProvider()
            .transcribe(audio_path=None, language_hint=LanguageHint.AUTO)
            .words,
        )
    return InMemoryPlanRepository(caption_repository), project_id, workspace_id, user_id


def test_create_plan_returns_valid_structured_plan() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()

    response = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(
                prompt="Make this feel premium but not over-edited.",
                style=EditPlanStyle.LUXURY,
                target_duration_s=20,
                broll_density=BrollDensity.MEDIUM,
            ),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    assert response.status == "ready"
    assert response.plan.version == 1
    assert response.plan.is_active is True
    assert response.plan.plan.style == EditPlanStyle.LUXURY
    assert response.plan.plan.cuts
    assert response.plan.plan.timeline is not None
    assert response.plan.plan.timeline.schema_version == 1
    assert response.plan.plan.timeline.metadata["authority"] == "project_timeline"
    assert [clip.output_start_ms for clip in response.plan.plan.timeline.clips] == sorted(
        clip.output_start_ms for clip in response.plan.plan.timeline.clips
    )
    assert all(cut.end_ms <= 12_000 for cut in response.plan.plan.cuts)
    assert response.plan.summary.cut_count == len(response.plan.plan.cuts)
    assert response.job_id.startswith(f"plan--{project_id}--")


def test_manual_elements_persist_in_versioned_edit_plan() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    created = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    element = ManualElement(
        id="title-1",
        type="text",
        name="Title Card",
        zone="canvas",
        start_ms=500,
        end_ms=3500,
        x_pct=50,
        y_pct=20,
        properties={"content": "Launch day", "color": "#ffffff"},
    )

    updated = asyncio.run(
        update_plan_manual_elements(
            project_id=project_id,
            plan_id=created.plan.id,
            payload=PlanManualElementsUpdateRequest(manual_elements=[element]),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
        )
    )

    assert updated.plan.version == created.plan.version
    assert updated.plan.plan.schema_version == 2
    assert updated.plan.plan.manual_elements[0].properties["content"] == "Launch day"
    assert updated.plan.plan.timeline is not None
    assert updated.plan.plan.timeline.manual_layers[0].id == "title-1"


def test_canonical_timeline_update_projects_to_legacy_clients() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    created = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    timeline = created.plan.plan.timeline
    assert timeline is not None
    clip = timeline.clips[0].model_copy(
        update={
            "source_start_ms": 1000,
            "source_end_ms": 3000,
            "output_start_ms": 0,
            "output_end_ms": 2000,
            "reason": "manual timeline edit",
        }
    )
    edited = timeline.model_copy(update={"clips": [clip], "duration_ms": 2000})

    response = asyncio.run(
        update_project_timeline(
            project_id=project_id,
            plan_id=created.plan.id,
            payload=TimelineUpdateRequest(timeline=edited),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
        )
    )

    assert response.plan.plan.timeline == edited
    assert response.plan.plan.cuts[0].start_ms == 1000
    assert response.plan.plan.cuts[0].end_ms == 3000


def test_compile_timeline_endpoint_persists_caption_layers() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    created = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    transcript = repository.latest_transcript(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    assert transcript is not None
    preset = get_caption_preset(CaptionPresetKey.FOUNDER_CLEAN)
    repository.caption_repository.create_caption_track(
        workspace_id=workspace_id,
        project_id=project_id,
        transcript_id=transcript.id,
        preset=CaptionPresetKey.FOUNDER_CLEAN,
        schema_version=CAPTION_SCHEMA_VERSION,
        style=preset.style,
        hide_filler_words=False,
        segments=chunk_caption_words(transcript.words),
    )

    response = asyncio.run(
        compile_project_timeline(
            project_id=project_id,
            plan_id=created.plan.id,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
        )
    )

    assert response.plan.plan.timeline is not None
    assert response.plan.plan.timeline.captions
    assert response.plan.plan.timeline.captions[0].start_ms >= 0


def test_create_plan_reuses_cache_without_force() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    payload = PlanCreateRequest(style=EditPlanStyle.FOUNDER, target_duration_s=25)

    first = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=payload,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    second = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=payload,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    assert second.cached is True
    assert second.plan.id == first.plan.id
    assert len(repository.list_plans(project_id=project_id, workspace_id=workspace_id)) == 1


def test_plan_controls_change_structured_output() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    luxury = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(
                prompt="Make this sharper for founders, preserve the educational points.",
                style=EditPlanStyle.LUXURY,
                target_duration_s=10,
                broll_density=BrollDensity.NONE,
                force=True,
            ),
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    travel = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(
                prompt="Make this sharper for founders, preserve the educational points.",
                style=EditPlanStyle.TRAVEL,
                target_duration_s=12,
                broll_density=BrollDensity.HIGH,
                force=True,
            ),
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    assert luxury.plan.plan.color_grade != travel.plan.plan.color_grade
    assert luxury.plan.summary.broll_count == 0
    assert travel.plan.summary.broll_count > luxury.plan.summary.broll_count
    assert travel.plan.summary.duration_s >= luxury.plan.summary.duration_s
    assert "meaning_preservation_requested" in luxury.plan.summary.risk_flags
    assert luxury.plan.summary.source_topic != "Transcript-driven edit"


def test_audio_intelligence_keywords_seed_fixture_broll_requests() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    transcript = repository.latest_transcript(project_id=project_id, workspace_id=workspace_id)
    assert transcript is not None
    repository.caption_repository.update_transcript(
        transcript=transcript,
        raw_payload={
            "audio_intelligence": {
                "keywords": [
                    {"text": "atelier", "score": 0.96},
                    {"text": "craft", "score": 0.91},
                ],
                "style_signals": {"predicted_style": "luxury"},
            }
        },
    )

    response = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(
                style=EditPlanStyle.LUXURY,
                broll_density=BrollDensity.MEDIUM,
                force=True,
            ),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    broll_keywords = response.plan.plan.broll_requests[0].keywords
    assert any("atelier" in keyword for keyword in broll_keywords)


def test_visual_manifest_changes_plan_cache_and_seeds_broll_requests() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    payload = PlanCreateRequest(style=EditPlanStyle.LUXURY, broll_density=BrollDensity.MEDIUM)

    first = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=payload,
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    upload = repository.get_active_upload(project_id=project_id, workspace_id=workspace_id)
    visual_repository = InMemoryVisualRepository(repository.caption_repository.upload_repository)
    repository.visual_repository = visual_repository
    visual_repository.create_manifest(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status=VisualManifestStatus.READY,
        provider="fixture_visual",
        model="fixture-visual-v1",
        manifest=_sample_visual_manifest(),
    )

    second = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=payload,
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    keywords = [
        keyword for request in second.plan.plan.broll_requests for keyword in request.keywords
    ]
    first_request = second.plan.plan.broll_requests[0]
    assert second.cached is False
    assert second.plan.version == first.plan.version + 1
    assert any("watch" in keyword or "macro" in keyword for keyword in keywords)
    assert first_request.semantic_query
    assert first_request.semantic_embedding == [0.1, 0.2, 0.3]
    assert first_request.source_scene_index == 0
    assert first_request.source_scene_type == SceneType.PRODUCT.value
    assert first_request.shot_type == "product_detail"
    assert first_request.avoid_face_overlap is True


def test_failed_visual_analysis_degrades_plan_with_explicit_risk_flag() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    upload = repository.get_active_upload(project_id=project_id, workspace_id=workspace_id)
    visual_repository = InMemoryVisualRepository(repository.caption_repository.upload_repository)
    repository.visual_repository = visual_repository
    visual_repository.create_manifest(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status=VisualManifestStatus.FAILED,
        provider="fixture_visual",
        model="fixture-visual-v1",
        manifest=None,
        error_code="visual_analysis_failed",
        error_message="Visual analysis failed; planning will continue without visual context.",
    )
    response = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(force=True),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    assert response.status == "ready"
    assert "visual_context_failed" in response.plan.plan.risk_flags
    assert "visual_context_failed" in response.plan.summary.risk_flags


def test_visual_failure_degrades_to_flagged_transcript_plan() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    upload = repository.get_active_upload(project_id=project_id, workspace_id=workspace_id)
    visual_repository = InMemoryVisualRepository(repository.caption_repository.upload_repository)
    repository.visual_repository = visual_repository
    visual_repository.create_manifest(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status=VisualManifestStatus.FAILED,
        provider="fixture_visual",
        model="fixture-visual-v1",
        manifest=None,
        error_code="visual_analysis_failed",
        error_message="Visual analysis failed; planning will continue without visual context.",
    )

    response = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(force=True),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    assert response.plan.status == EditPlanStatus.READY
    assert "visual_context_failed" in response.plan.plan.risk_flags
    assert "visual_context_failed" in response.plan.summary.risk_flags


def test_auto_style_classifier_selects_educational_from_audio_and_visual_context() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    transcript = repository.latest_transcript(project_id=project_id, workspace_id=workspace_id)
    assert transcript is not None
    repository.caption_repository.update_transcript(
        transcript=transcript,
        raw_payload={
            "audio_intelligence": {
                "keywords": [
                    {"text": "framework", "score": 0.94},
                    {"text": "steps", "score": 0.9},
                ],
                "style_signals": {
                    "predicted_style": "educational",
                    "tone": "neutral",
                    "pace": "medium",
                },
                "speech_metrics": {"words_per_minute": 138},
            }
        },
    )
    upload = repository.get_active_upload(project_id=project_id, workspace_id=workspace_id)
    visual_repository = InMemoryVisualRepository(repository.caption_repository.upload_repository)
    repository.visual_repository = visual_repository
    visual_repository.create_manifest(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status=VisualManifestStatus.READY,
        provider="fixture_visual",
        model="fixture-visual-v1",
        manifest=_educational_visual_manifest(),
    )

    response = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(prompt="Keep it clear and useful."),
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    classification = response.plan.summary.style_classification
    assert response.plan.style == EditPlanStyle.EDUCATIONAL
    assert response.plan.plan.style == EditPlanStyle.EDUCATIONAL
    assert classification is not None
    assert classification.predicted_style == EditPlanStyle.EDUCATIONAL
    assert classification.effective_style == EditPlanStyle.EDUCATIONAL
    assert classification.requested_style is None
    assert "auto_style" in classification.signals
    assert "visual_manifest" in classification.signals
    assert response.plan.plan.color_grade == "neutral_daylight"


def test_legacy_style_request_is_disambiguated_to_stage_3_style() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()

    response = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.FOUNDER, force=True),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    classification = response.plan.summary.style_classification
    assert response.plan.style in {
        EditPlanStyle.CLEAN_MINIMAL,
        EditPlanStyle.CREATOR_INFLUENCER,
    }
    assert response.plan.plan.style == response.plan.style
    assert classification is not None
    assert classification.requested_style == EditPlanStyle.FOUNDER
    assert classification.effective_style == response.plan.style
    assert "legacy_style:founder" in classification.signals


def test_regeneration_feedback_changes_plan_without_replacing_previous_version() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    first = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(
                style=EditPlanStyle.FOUNDER,
                broll_density=BrollDensity.MEDIUM,
            ),
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    regenerated = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(
                style=EditPlanStyle.FOUNDER,
                broll_density=BrollDensity.MEDIUM,
                feedback="Use fewer zooms and more premium B-roll.",
                force=True,
            ),
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    assert regenerated.plan.version == 2
    assert regenerated.plan.feedback == "Use fewer zooms and more premium B-roll."
    assert regenerated.plan.summary.zoom_count < first.plan.summary.zoom_count
    assert regenerated.plan.summary.broll_count > first.plan.summary.broll_count
    assert len(repository.list_plans(project_id=project_id, workspace_id=workspace_id)) == 2


def test_activate_plan_restores_previous_version() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    first = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.PODCAST),
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    second = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.TRAVEL, force=True),
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    restored = asyncio.run(
        activate_plan(
            project_id=project_id,
            plan_id=first.plan.id,
            actor=actor,
            repository=repository,
        )
    )
    listed = asyncio.run(list_plans(project_id=project_id, actor=actor, repository=repository))

    assert second.plan.is_active is True
    assert restored.plan.id == first.plan.id
    assert listed.active_plan_id == first.plan.id


def test_plan_snapshot_save_list_and_restore_creates_new_active_plan() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    created = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.LUXURY),
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    trimmed_cuts = [
        {
            "start_ms": cut.start_ms,
            "end_ms": cut.end_ms,
            "reason": f"snapshot {index}",
        }
        for index, cut in enumerate(created.plan.plan.cuts[:2])
    ]
    updated = asyncio.run(
        update_plan_cuts(
            project_id=project_id,
            plan_id=created.plan.id,
            payload=PlanCutsUpdateRequest(cuts=trimmed_cuts),
            actor=actor,
            repository=repository,
        )
    )

    snapshot = asyncio.run(
        create_plan_snapshot(
            project_id=project_id,
            payload=PlanSnapshotCreateRequest(
                plan_id=created.plan.id,
                name="Client-approved draft",
                notes="Keep this cut order.",
            ),
            actor=actor,
            repository=repository,
        )
    )
    listed = asyncio.run(
        list_plan_snapshots(project_id=project_id, actor=actor, repository=repository)
    )
    restored = asyncio.run(
        restore_plan_snapshot(
            project_id=project_id,
            snapshot_id=snapshot.snapshot.id,
            actor=actor,
            repository=repository,
        )
    )

    assert snapshot.snapshot.name == "Client-approved draft"
    assert snapshot.snapshot.source_plan_id == created.plan.id
    assert listed.snapshots[0].id == snapshot.snapshot.id
    assert restored.plan.id != created.plan.id
    assert restored.plan.is_active is True
    assert restored.plan.version == created.plan.version + 1
    assert restored.plan.plan.cuts[0].reason == updated.plan.plan.cuts[0].reason
    assert restored.plan.provider == "motionly_snapshot"


def test_update_plan_cuts_persists_trim_split_delete_and_reorder() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    created = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.PODCAST),
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    original = created.plan.plan.cuts
    first = original[0]
    second = original[1] if len(original) > 1 else first

    updated = asyncio.run(
        update_plan_cuts(
            project_id=project_id,
            plan_id=created.plan.id,
            payload=PlanCutsUpdateRequest(
                cuts=[
                    {
                        "start_ms": second.start_ms,
                        "end_ms": second.end_ms,
                        "reason": "moved first",
                    },
                    {
                        "start_ms": first.start_ms,
                        "end_ms": first.start_ms + 900,
                        "reason": "trimmed intro",
                    },
                    {
                        "start_ms": first.start_ms + 900,
                        "end_ms": min(first.end_ms, first.start_ms + 1800),
                        "reason": "split intro",
                    },
                ],
            ),
            actor=actor,
            repository=repository,
        )
    )

    saved = repository.get_plan(
        project_id=project_id,
        workspace_id=workspace_id,
        plan_id=created.plan.id,
    )
    assert updated.plan.summary.cut_count == 3
    assert saved.plan.cuts[0].reason == "moved first"
    assert saved.plan.cuts[1].end_ms - saved.plan.cuts[1].start_ms == 900
    assert saved.target_duration_s == updated.plan.target_duration_s


def test_update_plan_cuts_rejects_cross_workspace_access() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    created = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.PODCAST),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            update_plan_cuts(
                project_id=project_id,
                plan_id=created.plan.id,
                payload=PlanCutsUpdateRequest(cuts=created.plan.plan.cuts),
                actor=Actor(user_id=user_id, workspace_id=uuid4()),
                repository=repository,
            )
        )

    assert exc_info.value.status_code in {403, 404}


def test_update_plan_cuts_validates_ranges() -> None:
    with pytest.raises(ValueError):
        PlanCutsUpdateRequest(
            cuts=[{"start_ms": 1000, "end_ms": 1000, "reason": "invalid trim"}],
        )


def test_create_plan_requires_transcript() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository(with_transcript=False)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            create_plan(
                project_id=project_id,
                payload=PlanCreateRequest(),
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                repository=repository,
                planner_provider=FixtureEditPlannerProvider(),
                plan_queue=PlanQueue(),
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "transcript_not_found"


def test_create_plan_rejects_cross_workspace_access() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            create_plan(
                project_id=project_id,
                payload=PlanCreateRequest(),
                actor=Actor(user_id=user_id, workspace_id=uuid4()),
                repository=repository,
                planner_provider=FixtureEditPlannerProvider(),
                plan_queue=PlanQueue(),
            )
        )

    assert exc_info.value.status_code in {403, 404}


def test_validation_errors_use_api_error_envelope() -> None:
    response = asyncio.run(
        request_validation_error_handler(
            SimpleNamespace(
                state=SimpleNamespace(request_id="req_test"),
                method="POST",
                url=SimpleNamespace(path="/projects/test/plans"),
            ),
            RequestValidationError(
                [
                    {
                        "type": "greater_than_equal",
                        "loc": ("body", "target_duration_s"),
                        "msg": "Input should be greater than or equal to 10",
                    }
                ]
            ),
        )
    )

    assert response.status_code == 422
    assert b"request_validation_failed" in response.body
    assert b"target_duration_s" in response.body


def test_plan_create_request_accepts_json_string_body() -> None:
    payload = PlanCreateRequest.model_validate_json(
        '{"prompt":"tight edit","style":"luxury","broll_density":"medium","force":true}'
    )

    assert payload.prompt == "tight edit"
    assert payload.force is True

    payload = PlanCreateRequest.model_validate(
        '{"prompt":"wrapped body","style":"founder","broll_density":"low"}'
    )

    assert payload.prompt == "wrapped body"
    assert payload.style == EditPlanStyle.FOUNDER


def test_http_planner_receives_audio_intelligence(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, project_id, workspace_id, _user_id = make_plan_repository()
    transcript = repository.latest_transcript(project_id=project_id, workspace_id=workspace_id)
    assert transcript is not None
    repository.caption_repository.update_transcript(
        transcript=transcript,
        raw_payload={
            "audio_intelligence": {
                "keywords": [{"text": "atelier", "score": 0.96}],
                "style_signals": {"predicted_style": "luxury"},
                "speech_metrics": {"words_per_minute": 140},
            }
        },
    )
    captured: dict[str, object] = {}

    def fake_post(*args: object, **kwargs: object):
        captured["json"] = kwargs["json"]
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "schema_version": 1,
                                    "style": "luxury",
                                    "target_duration_s": 10,
                                    "cuts": [{"start_ms": 240, "end_ms": 3500, "reason": "hook"}],
                                    "caption_highlights": [],
                                    "broll_requests": [],
                                    "zoom_moments": [],
                                    "music_mood": "clean",
                                    "effects": [],
                                    "color_grade": "neutral",
                                    "risk_flags": [],
                                }
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 100},
            },
        )

    monkeypatch.setattr("app.services.edit_planner.httpx.post", fake_post)
    HttpEditPlannerProvider(
        endpoint="https://planner.test",
        api_key="test",
        model="test-model",
    ).create_plan(
        transcript=transcript,
        payload=PlanCreateRequest(style=EditPlanStyle.LUXURY, target_duration_s=10),
        video_duration_ms=12_000,
        visual_manifest=_sample_visual_manifest(),
        style_classification=StyleClassification(
            predicted_style=EditPlanStyle.LUXURY,
            effective_style=EditPlanStyle.LUXURY,
            requested_style=EditPlanStyle.LUXURY,
            confidence=0.82,
            scores={"luxury": 1.0},
            signals=["audio_style:luxury", "visual_manifest"],
        ),
    )

    request_json = captured["json"]
    assert isinstance(request_json, dict)
    message = request_json["messages"][1]
    assert isinstance(message, dict)
    content = json.loads(str(message["content"]))
    assert content["audio_intelligence"]["keywords"][0]["text"] == "atelier"
    assert content["style_classification"]["effective_style"] == "luxury"
    assert content["style_classification"]["signals"] == [
        "audio_style:luxury",
        "visual_manifest",
    ]
    assert content["visual_intelligence"]["top_scenes"][0]["objects"] == ["watch"]
    assert content["visual_intelligence"]["top_scenes"][0]["scene_type"] == "product"


def test_http_planner_repairs_invalid_provider_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, project_id, workspace_id, _user_id = make_plan_repository()
    transcript = repository.latest_transcript(project_id=project_id, workspace_id=workspace_id)
    assert transcript is not None

    def fake_post(*args: object, **kwargs: object):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": """
                            {
                              "plan": {
                                "style": "LUXURY",
                                "target_duration_s": 8,
                                "cuts": [{"start_ms": 0, "end_ms": 3500, "reason": "hook"}],
                                "caption_highlights": [
                                  {"timestamp_ms": 500, "word": "Hello", "effect": "pop"}
                                ],
                                "b_roll_requests": [
                                  {
                                    "start_ms": 700,
                                    "end_ms": 2400,
                                    "keywords": "office",
                                    "priority": 2
                                  }
                                ],
                                "zoom_moments": [
                                  {
                                    "timestamp_ms": 600,
                                    "intensity": 2,
                                    "reason": "focus"
                                  }
                                ],
                                "music_mood": "clean",
                                "effects": "warm_grade",
                                "color_grade": "neutral",
                                "risk_flags": []
                              }
                            }
                            """
                        }
                    }
                ],
                "usage": {"total_tokens": 100},
            },
        )

    monkeypatch.setattr("app.services.edit_planner.httpx.post", fake_post)
    result = HttpEditPlannerProvider(
        endpoint="https://planner.test",
        api_key="test",
        model="test-model",
        provider_name="openrouter",
    ).create_plan(
        transcript=transcript,
        payload=PlanCreateRequest(style=EditPlanStyle.LUXURY, target_duration_s=10),
        video_duration_ms=12_000,
    )

    assert result.plan.style == EditPlanStyle.LUXURY
    assert result.plan.target_duration_s == 10
    assert result.plan.caption_highlights[0].effect == "pulse"
    assert result.validation_errors
    assert "repair_used" in result.validation_errors[-1]


def test_http_planner_falls_back_and_records_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, project_id, workspace_id, _user_id = make_plan_repository()
    transcript = repository.latest_transcript(project_id=project_id, workspace_id=workspace_id)
    assert transcript is not None

    def fake_post(*args: object, **kwargs: object):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "not json"}}], "usage": {}},
        )

    monkeypatch.setattr("app.services.edit_planner.httpx.post", fake_post)
    result = HttpEditPlannerProvider(
        endpoint="https://planner.test",
        api_key="test",
        model="test-model",
        provider_name="openrouter",
    ).create_plan(
        transcript=transcript,
        payload=PlanCreateRequest(style=EditPlanStyle.FOUNDER, target_duration_s=10),
        video_duration_ms=12_000,
    )

    assert result.plan.style == EditPlanStyle.FOUNDER
    assert result.plan.cuts
    assert result.validation_errors
    assert result.raw_payload["fallback_used"] is True


def _sample_visual_manifest() -> VideoManifest:
    return VideoManifest(
        duration_ms=12_000,
        sample_rate_fps=2,
        frame_count=2,
        provider="fixture_visual",
        model_versions={
            "clip": "fixture",
            "blip": "fixture",
            "object_detection": "fixture",
            "face_detection": "fixture",
        },
        scenes=[
            SceneSegment(
                index=0,
                start_ms=0,
                end_ms=5_000,
                representative_timestamp_ms=1_000,
                caption="handmade watch macro detail",
                embedding=[0.1, 0.2, 0.3],
                dominant_colors=[DominantColor(hex="#111827", rgb=(17, 24, 39), percentage=0.7)],
                objects=[
                    ObjectDetection(
                        label="watch",
                        confidence=0.91,
                        box=BoundingBox(x=0.2, y=0.2, width=0.4, height=0.3),
                    )
                ],
                camera_motion=CameraMotion.PUSH_IN,
                scene_type=SceneType.PRODUCT,
                visual_importance_score=0.92,
            )
        ],
        summary={
            "scene_count": 1,
            "sample_count": 2,
            "scene_types": {"product": 1},
            "top_objects": ["watch"],
        },
    )


def _educational_visual_manifest() -> VideoManifest:
    return VideoManifest(
        duration_ms=12_000,
        sample_rate_fps=2,
        frame_count=2,
        provider="fixture_visual",
        model_versions={
            "clip": "fixture",
            "blip": "fixture",
            "object_detection": "fixture",
            "face_detection": "fixture",
        },
        scenes=[
            SceneSegment(
                index=0,
                start_ms=0,
                end_ms=6_000,
                representative_timestamp_ms=1_200,
                caption="screen tutorial showing three clear steps",
                embedding=[0.1, 0.2, 0.3],
                dominant_colors=[
                    DominantColor(hex="#f8fafc", rgb=(248, 250, 252), percentage=0.62)
                ],
                objects=[
                    ObjectDetection(
                        label="laptop",
                        confidence=0.88,
                        box=BoundingBox(x=0.12, y=0.18, width=0.7, height=0.48),
                    )
                ],
                camera_motion=CameraMotion.STATIC,
                scene_type=SceneType.SCREEN_RECORDING,
                visual_importance_score=0.9,
            )
        ],
        summary={
            "scene_count": 1,
            "sample_count": 2,
            "scene_types": {"screen_recording": 1},
            "top_objects": ["laptop"],
        },
    )


def test_async_planner_fast_path_and_polling() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    plan_queue = PlanQueue()
    
    # Mocking planner_provider to verify it is NOT called during HTTP route execution
    class MockFailurePlanner:
        provider_name = "mock_failure"
        def create_plan(self, *args, **kwargs):
            raise AssertionError("Should not be called synchronously in API route!")
            
    # Mock Request and Response
    class DummyResponse:
        def __init__(self):
            self.status_code = 200
    mock_response = DummyResponse()
    
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    media_work_dir=".motionly/test-storage",
                    transcription_debug_retain_audio=False,
                ),
                plan_repository=repository,
                edit_planner_provider=MockFailurePlanner(),
                operations_repository=None,
                queue_registry=plan_queue.registry,
            )
        )
    )

    # 1. POST /plans returns 202 on cache miss.
    # 2. OpenRouter/httpx.post is not called (proved by MockFailurePlanner not throwing)
    response = asyncio.run(
        _create_plan_api(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.LUXURY, force=True),
            actor=actor,
            repository=repository,
            planner_provider=MockFailurePlanner(),
            plan_queue=plan_queue,
            response=mock_response,
            request=request,
        )
    )
    
    assert mock_response.status_code == 202
    assert response.status == "queued"
    assert response.job_id.startswith(f"plan--{project_id}--")
    assert response.status_url == f"/projects/{project_id}/plans/jobs/{response.job_id}"

    # 3. GET /projects/{project_id}/plans/jobs/{job_id} returns queued
    status_response = asyncio.run(
        get_plan_job_status(
            project_id=project_id,
            job_id=response.job_id,
            actor=actor,
            repository=repository,
            request=request,
        )
    )
    assert status_response.status == "queued"
    assert status_response.progress == 0

    # 4. edit_plan job handler eventually persists a plan.
    from app.api.internal_jobs import _execute_job
    job = plan_queue.registry.jobs[response.job_id]
    
    execute_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    media_work_dir=".motionly/test-storage",
                    transcription_debug_retain_audio=False,
                ),
                plan_repository=repository,
                edit_planner_provider=FixtureEditPlannerProvider(),
                operations_repository=None,
                queue_registry=plan_queue.registry,
            )
        )
    )
    
    # Mark the job as running/started in registry
    plan_queue.registry.start(response.job_id)
    
    status_response = asyncio.run(
        get_plan_job_status(
            project_id=project_id,
            job_id=response.job_id,
            actor=actor,
            repository=repository,
            request=execute_request,
        )
    )
    assert status_response.status == "running"
    assert status_response.progress == 50

    # Execute
    result = _execute_job(job=job, request=execute_request)
    plan_queue.registry.complete(response.job_id)
    
    assert "plan_id" in result
    assert result["status"] == "ready"
    
    # Proves the plan was persisted in repository
    persisted_plan = repository.get_plan(
        project_id=project_id,
        workspace_id=workspace_id,
        plan_id=UUID(result["plan_id"]),
    )
    assert persisted_plan.status == EditPlanStatus.READY

    # 5. GET status is succeeded after worker completes
    status_response = asyncio.run(
        get_plan_job_status(
            project_id=project_id,
            job_id=response.job_id,
            actor=actor,
            repository=repository,
            request=execute_request,
        )
    )
    assert status_response.status == "succeeded"
    assert status_response.progress == 100
    assert status_response.plan_id == persisted_plan.id

    # 6. Cache hit still returns existing cached plan synchronously
    mock_response_cache = DummyResponse()
    cached_response = asyncio.run(
        _create_plan_api(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.LUXURY, force=False),
            actor=actor,
            repository=repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=plan_queue,
            response=mock_response_cache,
            request=execute_request,
        )
    )
    assert cached_response.cached is True
    assert cached_response.status == "ready"
    assert cached_response.plan.id == persisted_plan.id


def test_async_planner_failed_job() -> None:
    repository, project_id, workspace_id, user_id = make_plan_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    plan_queue = PlanQueue()
    
    class BrokenPlanner:
        provider_name = "broken"
        def create_plan(self, *args, **kwargs):
            raise RuntimeError("OpenRouter failed!")
            
    # Mock Request and Response
    class DummyResponse:
        def __init__(self):
            self.status_code = 200
    mock_response = DummyResponse()
    
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    media_work_dir=".motionly/test-storage",
                    transcription_debug_retain_audio=False,
                ),
                plan_repository=repository,
                edit_planner_provider=BrokenPlanner(),
                operations_repository=None,
                queue_registry=plan_queue.registry,
            )
        )
    )
    
    response = asyncio.run(
        _create_plan_api(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.LUXURY, force=True),
            actor=actor,
            repository=repository,
            planner_provider=BrokenPlanner(),
            plan_queue=plan_queue,
            response=mock_response,
            request=request,
        )
    )
    
    from app.api.internal_jobs import _execute_job
    job = plan_queue.registry.jobs[response.job_id]
    
    with pytest.raises(RuntimeError):
        _execute_job(job=job, request=request)
        
    plan_queue.registry.fail(response.job_id)
    plan_queue.registry.fail(response.job_id)
    plan_queue.registry.fail(response.job_id)

    # 7. failed edit_plan job produces failed status
    status_response = asyncio.run(
        get_plan_job_status(
            project_id=project_id,
            job_id=response.job_id,
            actor=actor,
            repository=repository,
            request=request,
        )
    )
    assert status_response.status == "failed"
    assert "OpenRouter failed!" in status_response.failure_reason


def test_phase9_cache_keys() -> None:
    from app.services.edit_planner import build_plan_cache_key
    from app.repositories.captions import TranscriptRecord
    from app.schemas.plans import PlanCreateRequest, EditPlanStyle
    from app.schemas.visual import VideoManifest
    from uuid import uuid4
    from datetime import datetime, UTC

    transcript = TranscriptRecord(
        id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        status="ready",
        language="en",
        language_hint="auto",
        provider="fixture",
        model="whisper-1",
        confidence=0.95,
        duration_ms=12_000,
        raw_payload={},
        words=[],
        updated_at=datetime.now(UTC),
    )

    payload = PlanCreateRequest(
        prompt="original prompt",
        style=EditPlanStyle.FOUNDER,
        target_duration_s=25,
        style_profile_id=uuid4(),
        saved_template_id=uuid4(),
    )

    manifest_1 = VideoManifest(
        duration_ms=12_000,
        sample_rate_fps=2,
        frame_count=2,
        provider="fixture_visual",
        summary={"title": "summary 1"},
    )
    manifest_2 = VideoManifest(
        duration_ms=12_000,
        sample_rate_fps=2,
        frame_count=2,
        provider="fixture_visual",
        summary={"title": "summary 2"},
    )

    # 1. Identical inputs => cache hit
    key1 = build_plan_cache_key(
        transcript=transcript,
        payload=payload,
        visual_manifest=manifest_1,
        style_classification=None,
        provider="openai",
        model="gpt-4o",
    )
    key2 = build_plan_cache_key(
        transcript=transcript,
        payload=payload,
        visual_manifest=manifest_1,
        style_classification=None,
        provider="openai",
        model="gpt-4o",
    )
    assert key1 == key2

    # 2. same transcript ID + changed full visual manifest hash => cache miss
    key_diff_manifest = build_plan_cache_key(
        transcript=transcript,
        payload=payload,
        visual_manifest=manifest_2,
        style_classification=None,
        provider="openai",
        model="gpt-4o",
    )
    assert key1 != key_diff_manifest

    # 3. changed user prompt => cache miss
    payload_diff_prompt = payload.model_copy(update={"prompt": "different prompt"})
    key_diff_prompt = build_plan_cache_key(
        transcript=transcript,
        payload=payload_diff_prompt,
        visual_manifest=manifest_1,
        style_classification=None,
        provider="openai",
        model="gpt-4o",
    )
    assert key1 != key_diff_prompt

    # 4. changed style_profile_id => cache miss
    payload_diff_style_profile = payload.model_copy(update={"style_profile_id": uuid4()})
    key_diff_style_profile = build_plan_cache_key(
        transcript=transcript,
        payload=payload_diff_style_profile,
        visual_manifest=manifest_1,
        style_classification=None,
        provider="openai",
        model="gpt-4o",
    )
    assert key1 != key_diff_style_profile

    # 5. changed saved_template_id => cache miss
    payload_diff_saved_template = payload.model_copy(update={"saved_template_id": uuid4()})
    key_diff_saved_template = build_plan_cache_key(
        transcript=transcript,
        payload=payload_diff_saved_template,
        visual_manifest=manifest_1,
        style_classification=None,
        provider="openai",
        model="gpt-4o",
    )
    assert key1 != key_diff_saved_template

    # 6. changed provider/model => cache miss
    key_diff_provider = build_plan_cache_key(
        transcript=transcript,
        payload=payload,
        visual_manifest=manifest_1,
        style_classification=None,
        provider="anthropic",
        model="gpt-4o",
    )
    assert key1 != key_diff_provider

    key_diff_model = build_plan_cache_key(
        transcript=transcript,
        payload=payload,
        visual_manifest=manifest_1,
        style_classification=None,
        provider="openai",
        model="gpt-4o-mini",
    )
    assert key1 != key_diff_model


def test_phase9_repair_vs_fallback() -> None:
    from app.services.edit_planner import _validate_or_repair_plan
    from app.repositories.captions import TranscriptRecord
    from app.schemas.plans import PlanCreateRequest, EditPlanStyle
    from app.schemas.captions import TranscriptWord
    from uuid import uuid4
    from datetime import datetime, UTC
    import json

    transcript = TranscriptRecord(
        id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        status="ready",
        language="en",
        language_hint="auto",
        provider="fixture",
        model="whisper-1",
        confidence=0.95,
        duration_ms=12_000,
        raw_payload={},
        words=[TranscriptWord(index=0, text="hello", start_ms=0, end_ms=1000, confidence=1.0)],
        updated_at=datetime.now(UTC),
    )

    payload = PlanCreateRequest(
        prompt="original prompt",
        style=EditPlanStyle.FOUNDER,
        target_duration_s=25,
    )

    # 1. Repairable malformed LLM output
    raw_plan_repairable = {
        "schema_version": 1,
        "style": "invalid_style_value",
        "target_duration_s": "45",
        "cuts": [
            {
                "start_ms": 100,
                "end_ms": 2000,
                "reason": "good cut",
            }
        ],
        "caption_highlights": [],
        "broll_requests": [],
        "zoom_moments": [],
        "music_mood": "chilled",
        "effects": ["flash", "zoom"],
        "color_grade": "warm_corporate",
        "risk_flags": [],
    }

    plan, validation_errors, fallback_used = _validate_or_repair_plan(
        raw_plan=raw_plan_repairable,
        transcript=transcript,
        payload=payload,
        video_duration_ms=12_000,
        provider_name="test_provider",
    )

    assert plan is not None
    assert fallback_used is False
    assert any("repair_applied" in err for err in validation_errors)
    assert plan.style == EditPlanStyle.FOUNDER
    assert plan.target_duration_s == 45

    # 2. Unrecoverable malformed output
    raw_plan_unrecoverable = {
        "schema_version": 1,
        "style": "founder",
        "target_duration_s": 25,
        "music_mood": "a" * 200,  # exceeds max_length=160, causing second validation to fail
    }

    plan, validation_errors, fallback_used = _validate_or_repair_plan(
        raw_plan=raw_plan_unrecoverable,
        transcript=transcript,
        payload=payload,
        video_duration_ms=12_000,
        provider_name="test_provider",
    )

    assert plan is not None
    assert fallback_used is True
    assert any("fallback_used" in err for err in validation_errors)
    assert any("validation_failed" in err for err in validation_errors)


def test_phase9_http_provider_json_decode_failure(monkeypatch) -> None:
    from app.services.edit_planner import HttpEditPlannerProvider
    from app.repositories.captions import TranscriptRecord
    from app.schemas.plans import PlanCreateRequest, EditPlanStyle
    from app.schemas.captions import TranscriptWord, LanguageHint
    from uuid import uuid4
    import json

    class MockResponse:
        def __init__(self, text: str, status_code: int = 200):
            self.text = text
            self.status_code = status_code
        def json(self):
            return {"choices": [{"message": {"content": self.text}}]}

    def mock_post(*args, **kwargs):
        return MockResponse("invalid json text { { {")

    monkeypatch.setattr("httpx.post", mock_post)

    provider = HttpEditPlannerProvider(
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        api_key="test-openrouter-key",
        model="openrouter/free",
        provider_name="openrouter"
    )

    transcript = TranscriptRecord(
        id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        status="ready",
        language="en",
        language_hint=LanguageHint.AUTO,
        provider="groq",
        model="whisper",
        confidence=1.0,
        duration_ms=5000,
        words=[TranscriptWord(index=0, text="hello", start_ms=0, end_ms=1000, confidence=1.0)],
        raw_payload={}
    )

    payload = PlanCreateRequest(
        style=EditPlanStyle.FOUNDER,
        prompt="Make it clean"
    )

    result = provider.create_plan(
        transcript=transcript,
        payload=payload,
        video_duration_ms=5000
    )

    assert result.raw_payload["fallback_used"] is True
    assert any("json_decode:" in err for err in result.validation_errors)
    assert any("json_decode_failed" in err for err in result.validation_errors)
    assert result.plan is not None

