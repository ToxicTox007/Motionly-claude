import asyncio
from uuid import uuid4

import pytest
from test_renders import make_render_repository

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.regeneration import regenerate_project_layers
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.regeneration import InMemoryRegenerationRepository
from app.schemas.regeneration import (
    ExportOptions,
    RegenerateRequest,
    RegenerationAction,
    RegenerationLayer,
    UserControlSettings,
)
from app.services.queue import RegenerationQueue


def test_regeneration_dag_preserves_transcription_for_broll_change() -> None:
    render_repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(render_repository.caption_repository)
    repository = InMemoryRegenerationRepository(plan_repository)

    response = asyncio.run(
        regenerate_project_layers(
            project_id=project_id,
            payload=RegenerateRequest(
                controls=UserControlSettings(broll_density=0.2),
                action=RegenerationAction.LESS_BROLL,
            ),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            regeneration_queue=RegenerationQueue(),
        )
    )

    assert response.status == "queued"
    assert response.creates_new_version is True
    assert response.affected_layers == [RegenerationLayer.BROLL, RegenerationLayer.RENDER]
    assert RegenerationLayer.TRANSCRIPT in response.preserved_layers
    assert response.controls.version == 1
    assert [artifact.version for artifact in response.artifact_versions] == [1, 1]
    assert response.job_id.startswith(f"regenerate:{project_id}:")


def test_regeneration_duration_change_invalidates_plan_downstream() -> None:
    render_repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(render_repository.caption_repository)
    repository = InMemoryRegenerationRepository(plan_repository)

    response = asyncio.run(
        regenerate_project_layers(
            project_id=project_id,
            payload=RegenerateRequest(
                controls=UserControlSettings(duration_preset="short"),
                export_options=ExportOptions(resolution="720p", platform="tiktok"),
            ),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            regeneration_queue=RegenerationQueue(),
        )
    )

    assert response.affected_layers == [
        RegenerationLayer.PLAN,
        RegenerationLayer.CUTS,
        RegenerationLayer.BROLL,
        RegenerationLayer.MUSIC,
        RegenerationLayer.EFFECTS,
        RegenerationLayer.RENDER,
    ]
    assert response.preserved_layers == [RegenerationLayer.TRANSCRIPT, RegenerationLayer.CAPTIONS]
    assert response.controls.export_options.resolution == "720p"


def test_regeneration_rejects_cross_workspace_access() -> None:
    render_repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(render_repository.caption_repository)
    repository = InMemoryRegenerationRepository(plan_repository)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            regenerate_project_layers(
                project_id=project_id,
                payload=RegenerateRequest(action=RegenerationAction.CHANGE_MUSIC),
                actor=Actor(user_id=user_id, workspace_id=uuid4()),
                repository=repository,
                regeneration_queue=RegenerationQueue(),
            )
        )

    assert exc_info.value.status_code in {403, 404}
