"""Task 30 — applying a creative variant changes the edit.

Covers: one test per variant dimension (the applied plan differs EXACTLY in
that dimension), stale-revision conflict handling (standard 409), and the
generate → apply → preview → render flow with the fast test renderer injected
the Task-15 way.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.creative_variants import (
    apply_creative_variant_direction,
    create_creative_variant_set,
)
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.revision_errors import (
    REVISION_CONFLICT_CODE,
    RevisionConflictError,
    revision_conflict_error_handler,
)
from app.repositories.scale import InMemoryScaleRepository
from app.schemas.captions import CaptionPresetKey
from app.schemas.creative_variants import (
    CreativeVariantApplyRequest,
    CreativeVariantDimension,
    CreativeVariantSetCreateRequest,
)
from app.schemas.plans import (
    BrollDensity,
    BrollRequest,
    CaptionHighlight,
    EditPlan,
    EditPlanCut,
    EditPlanResponse,
    EditPlanStyle,
)
from app.services.creative_variants import apply_creative_variant, build_creative_variants
from app.services.edit_planner import summarize_plan


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _plan_summary(plan: EditPlan):
    return summarize_plan(plan, style_classification=None)


def _base_plan() -> EditPlan:
    return EditPlan(
        style=EditPlanStyle.CREATOR_INFLUENCER,
        target_duration_s=20,
        cuts=[
            EditPlanCut(start_ms=0, end_ms=6_000, reason="show the product result"),
            EditPlanCut(start_ms=6_000, end_ms=14_000, reason="explain the proof"),
        ],
        caption_highlights=[CaptionHighlight(timestamp_ms=400, word="results", effect="pulse")],
        broll_requests=[
            BrollRequest(start_ms=0, end_ms=1_000, keywords=["a"], priority=0.9),
            BrollRequest(start_ms=1_000, end_ms=2_000, keywords=["b"], priority=0.8),
            BrollRequest(start_ms=2_000, end_ms=3_000, keywords=["c"], priority=0.7),
            BrollRequest(start_ms=3_000, end_ms=4_000, keywords=["d"], priority=0.6),
        ],
        zoom_moments=[],
        music_mood="confident",
        effects=[],
        color_grade="clean_founder",
    )


def _variant_for(
    dimension: CreativeVariantDimension, *, plan: EditPlan, density=BrollDensity.MEDIUM
):
    variants = build_creative_variants(
        plan=plan,
        broll_density=density,
        dimensions=[dimension],
        variant_count=1,
    )
    return variants[0]


def _dimension_touched_fields(plan: EditPlan, other: EditPlan) -> set[str]:
    """Return the top-level EditPlan field names that differ between two plans."""
    a = plan.model_dump(mode="json")
    b = other.model_dump(mode="json")
    return {key for key in a if a[key] != b[key]}


# --- one test per variant dimension: applied plan differs EXACTLY there ------


def test_apply_pacing_variant_changes_only_cuts_and_duration() -> None:
    plan = _base_plan()
    variant = _variant_for(CreativeVariantDimension.PACING, plan=plan)
    assert variant.pacing.cut_duration_multiplier != 1  # a real pacing change

    result = apply_creative_variant(plan=plan, variant=variant, broll_density=BrollDensity.MEDIUM)

    # Pacing owns cuts (durations) and the recomputed target duration only.
    assert _dimension_touched_fields(plan, result.plan) == {"cuts", "target_duration_s"}
    original = [c.end_ms - c.start_ms for c in plan.cuts]
    applied = [c.end_ms - c.start_ms for c in result.plan.cuts]
    assert applied != original
    assert result.broll_density == BrollDensity.MEDIUM


def test_apply_caption_style_variant_changes_only_caption_preset() -> None:
    plan = _base_plan()
    assert plan.caption_preset is None
    variant = _variant_for(CreativeVariantDimension.CAPTION_STYLE, plan=plan)

    result = apply_creative_variant(plan=plan, variant=variant, broll_density=BrollDensity.MEDIUM)

    assert _dimension_touched_fields(plan, result.plan) == {"caption_preset"}
    assert result.plan.caption_preset == variant.caption.preset
    assert isinstance(result.plan.caption_preset, CaptionPresetKey)


def test_apply_broll_variant_changes_only_broll_requests_and_density() -> None:
    plan = _base_plan()
    # Pick the LOW-density B-roll alternative so the trim is deterministic and
    # observable on plan content (HIGH would keep the already-full request list).
    variants = build_creative_variants(
        plan=plan,
        broll_density=BrollDensity.HIGH,
        dimensions=[CreativeVariantDimension.BROLL],
        variant_count=3,
    )
    variant = next(v for v in variants if v.broll.density == BrollDensity.LOW)

    result = apply_creative_variant(plan=plan, variant=variant, broll_density=BrollDensity.HIGH)

    # B-roll owns broll_requests (plan content); density lives on the record.
    assert _dimension_touched_fields(plan, result.plan) == {"broll_requests"}
    assert len(result.plan.broll_requests) == 1
    assert result.broll_density == BrollDensity.LOW


def test_apply_hook_variant_changes_only_hook_text_and_opening_highlight() -> None:
    plan = _base_plan()
    variant = _variant_for(CreativeVariantDimension.HOOK, plan=plan)

    result = apply_creative_variant(plan=plan, variant=variant, broll_density=BrollDensity.MEDIUM)

    assert _dimension_touched_fields(plan, result.plan) == {"hook_text", "caption_highlights"}
    assert result.plan.hook_text == variant.hook.text


def test_apply_opening_frame_variant_changes_only_opening_frame_and_first_cut() -> None:
    plan = _base_plan()
    # Make the opening-frame start distinct so the first cut actually moves.
    variant = _variant_for(CreativeVariantDimension.OPENING_FRAME, plan=plan)
    variant = variant.model_copy(
        update={"opening_frame": variant.opening_frame.model_copy(update={"start_ms": 500})}
    )

    result = apply_creative_variant(plan=plan, variant=variant, broll_density=BrollDensity.MEDIUM)

    assert _dimension_touched_fields(plan, result.plan) == {"opening_frame", "cuts"}
    assert result.plan.opening_frame is not None
    assert result.plan.cuts[0].start_ms == 500
    # Only the first cut moved; the rest are untouched.
    assert result.plan.cuts[1:] == plan.cuts[1:]


# --- route: conflict handling + generate/apply/preview/render ----------------


class _StubPlanRepository:
    """Minimal plan repo: an active plan with revision-enforced CAS updates."""

    def __init__(self, plan_record) -> None:
        self._record = plan_record

    def get_project_for_workspace(self, **_kwargs):
        return None

    def active_plan(self, **_kwargs):
        return self._record

    def get_plan(self, **_kwargs):
        return self._record

    def update_plan_with_revision(
        self, *, plan_id, expected_revision, new_plan, summary, **_kwargs
    ):
        if self._record.revision != expected_revision:
            raise RevisionConflictError(
                plan_id=str(plan_id),
                expected=expected_revision,
                actual=self._record.revision,
            )
        self._record.plan = new_plan
        self._record.summary = summary
        self._record.revision += 1
        return self._record


def _route_context():
    workspace_id = uuid4()
    project_id = uuid4()
    transcript_id = uuid4()
    plan_record = SimpleNamespace(
        id=uuid4(),
        workspace_id=workspace_id,
        project_id=project_id,
        transcript_id=transcript_id,
        revision=4,
        version=1,
        broll_density=BrollDensity.MEDIUM,
        plan=_base_plan(),
        summary=SimpleNamespace(style_classification=None),
        to_response=None,
    )

    def _to_response():
        from app.schemas.plans import EditPlanStatus

        return EditPlanResponse(
            id=plan_record.id,
            workspace_id=workspace_id,
            project_id=project_id,
            transcript_id=transcript_id,
            version=plan_record.version,
            schema_version=plan_record.plan.schema_version,
            status=EditPlanStatus.READY,
            is_active=True,
            style=plan_record.plan.style,
            target_duration_s=plan_record.plan.target_duration_s,
            broll_density=plan_record.broll_density,
            prompt="",
            provider="fixture",
            model="fixture",
            cache_key="cache",
            schema_hash="hash",
            summary=_plan_summary(plan_record.plan),
            plan=plan_record.plan,
            revision=plan_record.revision,
            created_at=_now(),
            updated_at=_now(),
        )

    plan_record.to_response = _to_response
    plan_repository = _StubPlanRepository(plan_record)
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
        plan_record=plan_record,
        plan_repository=plan_repository,
        scale_repository=scale_repository,
        request=request,
    )


def _generate_set(context, dimension: CreativeVariantDimension):
    return asyncio.run(
        create_creative_variant_set(
            project_id=context.project_id,
            payload=CreativeVariantSetCreateRequest(
                dimensions=[dimension],
                variant_count=1,
            ),
            actor=context.actor,
            request=context.request,
            plan_repository=context.plan_repository,
        )
    )


def test_apply_route_creates_a_new_plan_version_from_the_variant() -> None:
    context = _route_context()
    created = _generate_set(context, CreativeVariantDimension.PACING)
    starting_revision = context.plan_record.revision

    response = asyncio.run(
        apply_creative_variant_direction(
            project_id=context.project_id,
            variant_set_id=created.id,
            payload=CreativeVariantApplyRequest(
                variant_id=created.variants[0].id,
                expected_revision=created.revision,
                expected_plan_revision=starting_revision,
                render=False,
            ),
            actor=context.actor,
            request=context.request,
            plan_repository=context.plan_repository,
        )
    )

    assert response.plan_revision == starting_revision + 1
    assert response.applied_dimension == CreativeVariantDimension.PACING
    assert response.render_enqueued is False
    assert response.render_job_id is None
    assert response.variant_set.review_status == "applied"
    assert response.variant_set.review_only is False
    assert response.variant_set.applied_variant_id == created.variants[0].id
    assert response.variant_set.applied_plan_revision == starting_revision + 1


def test_apply_route_stale_plan_revision_is_a_standard_conflict() -> None:
    context = _route_context()
    created = _generate_set(context, CreativeVariantDimension.CAPTION_STYLE)

    with pytest.raises(RevisionConflictError) as exc_info:
        asyncio.run(
            apply_creative_variant_direction(
                project_id=context.project_id,
                variant_set_id=created.id,
                payload=CreativeVariantApplyRequest(
                    variant_id=created.variants[0].id,
                    expected_revision=created.revision,
                    expected_plan_revision=context.plan_record.revision - 1,
                    render=False,
                ),
                actor=context.actor,
                request=context.request,
                plan_repository=context.plan_repository,
            )
        )
    # The raised error maps to the ONE canonical 409 envelope (Task 14).
    exc = exc_info.value
    assert exc.expected == context.plan_record.revision - 1
    handler_request = SimpleNamespace(state=SimpleNamespace(request_id="req-1"))
    response = asyncio.run(revision_conflict_error_handler(handler_request, exc))
    assert response.status_code == 409
    import json

    body = json.loads(response.body)
    assert body["error"]["code"] == REVISION_CONFLICT_CODE
    assert body["current_revision"] == context.plan_record.revision
    # The applied variant set was NOT advanced when the plan write conflicted.
    stored = context.scale_repository.get(
        resource_id=created.id,
        workspace_id=context.workspace_id,
    )
    assert stored.revision == created.revision


def test_apply_route_stale_variant_set_revision_conflicts() -> None:
    context = _route_context()
    created = _generate_set(context, CreativeVariantDimension.HOOK)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            apply_creative_variant_direction(
                project_id=context.project_id,
                variant_set_id=created.id,
                payload=CreativeVariantApplyRequest(
                    variant_id=created.variants[0].id,
                    expected_revision=created.revision + 9,
                    expected_plan_revision=context.plan_record.revision,
                    render=False,
                ),
                actor=context.actor,
                request=context.request,
                plan_repository=context.plan_repository,
            )
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "creative_variant_set_revision_conflict"


def test_generate_apply_preview_render_end_to_end(tmp_path, monkeypatch) -> None:
    """generate → apply → preview → render with the fast test renderer injected
    the Task-15 way (settings.should_complete_renders_synchronously, no renderer
    setting; FastTestRenderer lives only in test code).

    The apply route looks up ``_create_render_target`` on the renders module, so
    we monkeypatch it to the test ``create_render`` helper that enqueues through
    the real route and then completes the job synchronously with the injected
    FastTestRenderer.
    """
    import app.api.creative_variants as cv_module
    from app.repositories.billing import InMemoryBillingRepository
    from app.repositories.broll import InMemoryBrollRepository
    from app.repositories.plans import InMemoryPlanRepository
    from app.repositories.scale import InMemoryScaleRepository
    from app.schemas.plans import BrollDensity as _BrollDensity
    from app.schemas.plans import PlanCreateRequest
    from app.schemas.renders import RenderJobStatus
    from app.services.queue import RenderQueue
    from tests.test_renders import create_render, make_render_repository
    from tests.worker_job_test_helpers import seed_active_plan

    render_repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(render_repository.caption_repository)
    seed_active_plan(
        plan_repository=plan_repository,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=PlanCreateRequest(target_duration_s=15, broll_density=_BrollDensity.MEDIUM),
    )
    active = plan_repository.active_plan(project_id=project_id, workspace_id=workspace_id)
    starting_plan_revision = active.revision

    # Inject the fast-completing render target the Task-15 way: the apply route's
    # render seam routes through the test create_render helper, which enqueues via
    # the real render route and completes the job synchronously with the injected
    # FastTestRenderer (chosen from settings, never from production code).
    async def _render_via_helper(*, project_id, plan_id, request, actor, plan_repository):
        from app.schemas.renders import RenderCreateRequest

        return await create_render(
            project_id=project_id,
            payload=RenderCreateRequest(edit_plan_id=plan_id),
            request=request,
            actor=actor,
            repository=request.app.state.render_repository,
            billing_repository=request.app.state.billing_repository,
            plan_repository=plan_repository,
            render_queue=request.app.state.render_queue,
            broll_repository=request.app.state.broll_repository,
        )

    monkeypatch.setattr(cv_module, "_render_applied_plan", _render_via_helper)

    scale_repository = InMemoryScaleRepository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                project_repository=SimpleNamespace(get_project=lambda **_kwargs: None),
                scale_repository=scale_repository,
                plan_repository=plan_repository,
                render_repository=render_repository,
                billing_repository=InMemoryBillingRepository(),
                broll_repository=InMemoryBrollRepository(plan_repository),
                render_queue=RenderQueue(),
                settings=SimpleNamespace(
                    export_storage_bucket="exports",
                    export_signed_url_ttl_seconds=300,
                    should_complete_renders_synchronously=True,
                    media_work_dir=str(tmp_path),
                    billing_enabled=False,
                    supabase_url=None,
                    supabase_service_role_key=None,
                ),
            )
        )
    )

    # generate
    created = asyncio.run(
        create_creative_variant_set(
            project_id=project_id,
            payload=CreativeVariantSetCreateRequest(
                dimensions=[CreativeVariantDimension.PACING],
                variant_count=1,
            ),
            actor=actor,
            request=request,
            plan_repository=plan_repository,
        )
    )

    # Preview-before-render: the apply route returns the new plan so the editor
    # can preview it; we render in the same call via render=True.
    response = asyncio.run(
        apply_creative_variant_direction(
            project_id=project_id,
            variant_set_id=created.id,
            payload=CreativeVariantApplyRequest(
                variant_id=created.variants[0].id,
                expected_revision=created.revision,
                expected_plan_revision=starting_plan_revision,
                render=True,
            ),
            actor=actor,
            request=request,
            plan_repository=plan_repository,
        )
    )

    # A new plan version exists and is what we preview.
    assert response.plan_revision == starting_plan_revision + 1
    assert response.applied_dimension == CreativeVariantDimension.PACING
    assert response.plan.revision == starting_plan_revision + 1

    # The render was queued against the NEW plan version and completed with the
    # injected FastTestRenderer (create_render helper completes synchronously).
    assert response.render_enqueued is True
    assert response.render_job_id is not None
    render_job = render_repository.get_render_job(
        project_id=project_id,
        render_job_id=response.render_job_id,
        workspace_id=workspace_id,
    )
    assert render_job.edit_plan_id == response.plan.id
    assert render_job.status == RenderJobStatus.COMPLETE
