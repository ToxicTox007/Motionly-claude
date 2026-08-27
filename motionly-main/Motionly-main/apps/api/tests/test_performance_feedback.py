import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.performance_feedback import ingest_performance_metric
from app.repositories.performance_feedback import InMemoryPerformanceFeedbackRepository
from app.schemas.performance_feedback import DurationBucket, PerformanceMetricIngestRequest
from app.schemas.publishing import PublishPlatform
from app.services.performance_feedback import (
    build_edit_feature_snapshot,
    build_performance_suggestions,
)


def _metric(
    repository,
    *,
    workspace_id,
    user_id,
    project_id,
    completion_rate,
    preset,
    style,
    duration_bucket,
):
    return repository.capture_metric(
        workspace_id=workspace_id,
        user_id=user_id,
        project_id=project_id,
        publish_job_id=uuid4(),
        platform=PublishPlatform.TIKTOK,
        observed_at=datetime.now(UTC),
        views=1000,
        average_view_duration_seconds=None,
        completion_rate=completion_rate,
        engagement_rate=0.1,
        caption_preset=preset,
        hook_style=style,
        duration_bucket=duration_bucket,
        duration_seconds=24,
        template_id="talking_head_reel_v1",
        captions_enabled=True,
    )


def test_performance_metrics_are_denied_until_explicit_consent_and_purged_on_revoke() -> None:
    repository = InMemoryPerformanceFeedbackRepository(default_retention_days=30)
    workspace_id, user_id, project_id = uuid4(), uuid4(), uuid4()

    assert (
        _metric(
            repository,
            workspace_id=workspace_id,
            user_id=user_id,
            project_id=project_id,
            completion_rate=0.7,
            preset="bold_influencer",
            style="fast_paced",
            duration_bucket=DurationBucket.SHORT,
        )
        is None
    )

    policy = repository.set_policy_if_revision(
        workspace_id=workspace_id,
        user_id=user_id,
        metrics_ingestion_enabled=True,
        suggestions_enabled=True,
        policy_version="performance-feedback/v1",
        retention_days=30,
        expected_revision=0,
    )
    captured = _metric(
        repository,
        workspace_id=workspace_id,
        user_id=user_id,
        project_id=project_id,
        completion_rate=0.7,
        preset="bold_influencer",
        style="fast_paced",
        duration_bucket=DurationBucket.SHORT,
    )
    assert captured is not None and captured.suggestion_eligible is True
    assert len(repository.list_eligible_metrics(workspace_id=workspace_id, user_id=user_id)) == 1

    revoked = repository.revoke(workspace_id=workspace_id, user_id=user_id, reason="test")
    assert revoked.revision == policy.revision + 1
    assert repository.list_eligible_metrics(workspace_id=workspace_id, user_id=user_id) == []
    assert repository.metrics == {}


def test_suggestions_are_aggregate_observations_with_allow_listed_edit_features_only() -> None:
    repository = InMemoryPerformanceFeedbackRepository()
    workspace_id, user_id, project_id = uuid4(), uuid4(), uuid4()
    repository.set_policy_if_revision(
        workspace_id=workspace_id,
        user_id=user_id,
        metrics_ingestion_enabled=True,
        suggestions_enabled=True,
        policy_version="performance-feedback/v1",
        retention_days=90,
        expected_revision=0,
    )
    for rate in (0.81, 0.76, 0.79):
        assert _metric(
            repository,
            workspace_id=workspace_id,
            user_id=user_id,
            project_id=project_id,
            completion_rate=rate,
            preset="bold_influencer",
            style="fast_paced",
            duration_bucket=DurationBucket.SHORT,
        )
    suggestions = build_performance_suggestions(
        repository.list_eligible_metrics(workspace_id=workspace_id, user_id=user_id),
        minimum_examples=3,
    )

    captions = next(item for item in suggestions if item.kind == "captions")
    assert captions.recommended_value == "bold_influencer"
    assert captions.sample_count == 3
    assert captions.is_observational is True
    assert "average" in captions.editor_instruction


def test_edit_feature_snapshot_excludes_content_and_provider_data() -> None:
    snapshot = build_edit_feature_snapshot(
        render_job=SimpleNamespace(caption_track_id=uuid4(), template_id="talking_head_reel_v1"),
        export=SimpleNamespace(duration_ms=24_000),
        plan=SimpleNamespace(
            style="fast_paced", prompt="secret source prompt", plan={"timeline": "secret"}
        ),
        caption_track=SimpleNamespace(preset="bold_influencer", segments=["secret transcript"]),
    )

    assert snapshot.as_dict() == {
        "caption_preset": "bold_influencer",
        "hook_style": "fast_paced",
        "duration_bucket": "under_30_seconds",
        "template_id": "talking_head_reel_v1",
        "captions_enabled": True,
    }


def test_expired_performance_metrics_are_physically_deleted_by_retention() -> None:
    from app.services.retention import RetentionService

    service = RetentionService(
        settings=SimpleNamespace(supabase_url=None, supabase_service_role_key=None),
        storage_signer=SimpleNamespace(),
    )
    calls: list[tuple[str, str, dict[str, object]]] = []

    def request(method, table, **kwargs):
        calls.append((method, table, kwargs))
        return [{"id": "expired-metric"}]

    service._request = request

    assert service._delete_expired_performance_feedback_metrics() == 1
    assert calls[0][0:2] == ("DELETE", "performance_feedback_metrics")
    assert calls[0][2]["params"]["expires_at"].startswith("lte.")


def test_performance_metric_endpoint_requires_explicit_consent() -> None:
    workspace_id, user_id, project_id = uuid4(), uuid4(), uuid4()
    repository = InMemoryPerformanceFeedbackRepository()
    state = SimpleNamespace(
        settings=SimpleNamespace(
            performance_feedback_enabled=True,
            performance_feedback_policy_version="performance-feedback/v1",
            performance_feedback_retention_days=90,
            performance_feedback_minimum_examples=3,
        ),
        performance_feedback_repository=repository,
        project_repository=SimpleNamespace(get_project=lambda **_kwargs: SimpleNamespace()),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    with pytest.raises(ApiError) as error:
        asyncio.run(
            ingest_performance_metric(
                project_id=project_id,
                publish_job_id=uuid4(),
                payload=PerformanceMetricIngestRequest(observed_at=datetime.now(UTC), views=12),
                request=request,
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
            )
        )

    assert error.value.status_code == 403
    assert error.value.code == "performance_metrics_consent_required"


def test_metric_contract_rejects_unapproved_audience_or_content_fields() -> None:
    with pytest.raises(ValidationError):
        PerformanceMetricIngestRequest.model_validate(
            {
                "observed_at": datetime.now(UTC),
                "views": 12,
                "comments": ["not retained"],
                "audience_demographics": {"age": "not retained"},
                "raw_provider_payload": {"not retained": True},
            }
        )
