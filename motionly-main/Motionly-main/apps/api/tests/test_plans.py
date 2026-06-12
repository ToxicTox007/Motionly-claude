import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.exceptions import RequestValidationError

from app.api.dependencies import Actor
from app.api.errors import ApiError, request_validation_error_handler
from app.api.plans import activate_plan, create_plan, list_plans, update_plan_cuts
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.captions import LanguageHint
from app.schemas.plans import BrollDensity, EditPlanStyle, PlanCreateRequest, PlanCutsUpdateRequest
from app.schemas.uploads import UploadStatus
from app.services.edit_planner import FixtureEditPlannerProvider, HttpEditPlannerProvider
from app.services.queue import PlanQueue
from app.services.transcription_provider import FixtureTranscriptionProvider


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
    assert all(cut.end_ms <= 12_000 for cut in response.plan.plan.cuts)
    assert response.plan.summary.cut_count == len(response.plan.plan.cuts)
    assert response.job_id.startswith(f"plan:{project_id}:")


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
            SimpleNamespace(state=SimpleNamespace(request_id="req_test")),
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
