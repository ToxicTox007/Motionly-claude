import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.creative_variants import (
    create_creative_variant_set,
    list_creative_variant_sets,
    select_creative_variant,
)
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.repositories.scale import InMemoryScaleRepository
from app.schemas.creative_variants import (
    CreativeVariantDimension,
    CreativeVariantSelectionRequest,
    CreativeVariantSetCreateRequest,
)
from app.schemas.plans import (
    BrollDensity,
    CaptionHighlight,
    EditPlan,
    EditPlanCut,
    EditPlanStyle,
)


def test_creative_variant_set_covers_selected_dimensions_and_never_enqueues_work() -> None:
    context = _context()
    response = asyncio.run(
        create_creative_variant_set(
            project_id=context.project_id,
            payload=CreativeVariantSetCreateRequest(
                name="Launch comparison",
                dimensions=list(CreativeVariantDimension),
                variant_count=5,
            ),
            actor=context.actor,
            request=context.request,
            plan_repository=context.plan_repository,
        )
    )

    assert response.review_only is True
    assert response.selected_variant_id is None
    assert response.source_plan_id == context.plan.id
    assert response.source_plan_revision == context.plan.revision
    assert {variant.primary_dimension for variant in response.variants} == set(
        CreativeVariantDimension
    )
    assert response.variants[0].hook.text != response.variants[1].hook.text
    stored = context.scale_repository.get(
        resource_id=response.id,
        workspace_id=context.workspace_id,
    )
    assert stored.kind == "creative_variant_set"
    assert stored.result == {
        "comparison_ready": True,
        "render_enqueued": False,
    }
    # Motionly does not publish to social platforms, so nothing mentions it.
    notes = " ".join(note for variant in response.variants for note in variant.comparison_notes)
    assert "publish" not in notes.lower()
    assert not hasattr(context.request.app.state, "scale_queue")


def test_creative_variant_selection_is_persisted_and_revision_safe() -> None:
    context = _context()
    created = asyncio.run(
        create_creative_variant_set(
            project_id=context.project_id,
            payload=CreativeVariantSetCreateRequest(
                dimensions=[CreativeVariantDimension.HOOK, CreativeVariantDimension.PACING],
                variant_count=2,
            ),
            actor=context.actor,
            request=context.request,
            plan_repository=context.plan_repository,
        )
    )
    selected = asyncio.run(
        select_creative_variant(
            project_id=context.project_id,
            variant_set_id=created.id,
            payload=CreativeVariantSelectionRequest(
                variant_id=created.variants[1].id,
                expected_revision=created.revision,
            ),
            actor=context.actor,
            request=context.request,
            plan_repository=context.plan_repository,
        )
    )

    assert selected.selected_variant_id == created.variants[1].id
    assert selected.review_status == "selected"
    assert selected.revision == created.revision + 1
    listed = asyncio.run(
        list_creative_variant_sets(
            project_id=context.project_id,
            actor=context.actor,
            request=context.request,
            plan_repository=context.plan_repository,
        )
    )
    assert listed.variant_sets[0].selected_variant_id == created.variants[1].id

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            select_creative_variant(
                project_id=context.project_id,
                variant_set_id=created.id,
                payload=CreativeVariantSelectionRequest(
                    variant_id=created.variants[0].id,
                    expected_revision=created.revision,
                ),
                actor=context.actor,
                request=context.request,
                plan_repository=context.plan_repository,
            )
        )
    assert exc_info.value.code == "creative_variant_set_revision_conflict"


def test_creative_variant_selection_does_not_cross_projects() -> None:
    context = _context()
    created = asyncio.run(
        create_creative_variant_set(
            project_id=context.project_id,
            payload=CreativeVariantSetCreateRequest(
                dimensions=[CreativeVariantDimension.HOOK],
                variant_count=1,
            ),
            actor=context.actor,
            request=context.request,
            plan_repository=context.plan_repository,
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            select_creative_variant(
                project_id=uuid4(),
                variant_set_id=created.id,
                payload=CreativeVariantSelectionRequest(
                    variant_id=created.variants[0].id,
                    expected_revision=created.revision,
                ),
                actor=context.actor,
                request=context.request,
                plan_repository=context.plan_repository,
            )
        )
    assert exc_info.value.code == "creative_variant_set_not_found"


def _context() -> SimpleNamespace:
    workspace_id = uuid4()
    project_id = uuid4()
    plan = SimpleNamespace(
        id=uuid4(),
        revision=4,
        broll_density=BrollDensity.MEDIUM,
        plan=EditPlan(
            style=EditPlanStyle.CREATOR_INFLUENCER,
            target_duration_s=35,
            cuts=[EditPlanCut(start_ms=0, end_ms=8_000, reason="show the product result")],
            caption_highlights=[CaptionHighlight(timestamp_ms=400, word="results", effect="pulse")],
            broll_requests=[],
            zoom_moments=[],
            music_mood="confident",
            effects=[],
            color_grade="clean_founder",
        ),
    )
    plan_repository = SimpleNamespace(
        get_project_for_workspace=lambda **_kwargs: None,
        active_plan=lambda **_kwargs: plan,
    )
    scale_repository = InMemoryScaleRepository()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                project_repository=SimpleNamespace(get_project=lambda **_kwargs: None),
                scale_repository=scale_repository,
            )
        )
    )
    return SimpleNamespace(
        workspace_id=workspace_id,
        project_id=project_id,
        actor=Actor(user_id=uuid4(), workspace_id=workspace_id),
        plan=plan,
        plan_repository=plan_repository,
        scale_repository=scale_repository,
        request=request,
    )
