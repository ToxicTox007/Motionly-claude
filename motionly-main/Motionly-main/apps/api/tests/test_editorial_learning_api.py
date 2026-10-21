import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.editorial_learning import get_memory_indicator, get_policy, get_status, set_policy
from app.api.errors import ApiError
from app.repositories.editorial_learning import InMemoryEditorialLearningRepository
from app.schemas.editorial_learning import EditorialLearningPolicyUpdateRequest


def _request(*, enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                editorial_learning_repository=InMemoryEditorialLearningRepository(
                    default_retention_days=60
                ),
                settings=SimpleNamespace(
                    editorial_learning_enabled=enabled,
                    editorial_learning_policy_version="editorial-consent/v1",
                    editorial_learning_retention_days=60,
                    editorial_learning_minimum_examples=25,
                ),
            )
        )
    )


def _actor() -> Actor:
    return Actor(user_id=uuid4(), workspace_id=uuid4())


def test_editorial_status_is_metadata_only_and_policy_defaults_deny() -> None:
    request = _request(enabled=False)
    actor = _actor()

    status = asyncio.run(get_status(request, actor))
    policy = asyncio.run(get_policy(request, actor))

    assert status.model_dump() == {
        "enabled": False,
        "policy_version": "editorial-consent/v1",
        "default_retention_days": 60,
        "minimum_examples": 25,
    }
    assert policy.memory_enabled is False
    assert policy.training_enabled is False
    assert policy.revision == 0


def test_editorial_policy_write_requires_feature_and_current_acknowledgement() -> None:
    actor = _actor()
    mismatch = EditorialLearningPolicyUpdateRequest(
        memory_enabled=True,
        training_enabled=True,
        policy_version="editorial-consent/old",
        expected_revision=0,
    )
    enabled_request = _request(enabled=True)

    with pytest.raises(ApiError) as mismatch_error:
        asyncio.run(set_policy(mismatch, enabled_request, actor))
    assert mismatch_error.value.status_code == 422
    assert mismatch_error.value.code == "editorial_learning_policy_version_required"

    disabled_request = _request(enabled=False)
    current = EditorialLearningPolicyUpdateRequest(
        memory_enabled=True,
        training_enabled=False,
        policy_version="editorial-consent/v1",
        expected_revision=0,
    )
    with pytest.raises(ApiError) as disabled_error:
        asyncio.run(set_policy(current, disabled_request, actor))
    assert disabled_error.value.status_code == 403
    assert disabled_error.value.code == "editorial_learning_disabled"


def test_editorial_policy_write_uses_revisioned_canonical_consent_version() -> None:
    request = _request(enabled=True)
    actor = _actor()

    policy = asyncio.run(
        set_policy(
            EditorialLearningPolicyUpdateRequest(
                memory_enabled=True,
                training_enabled=True,
                policy_version="editorial-consent/v1",
                expected_revision=0,
                retention_days=180,
            ),
            request,
            actor,
        )
    )

    assert policy.memory_enabled is True
    assert policy.training_enabled is True
    assert policy.policy_version == "editorial-consent/v1"
    assert policy.revision == 1
    assert policy.retention_days == 180


def test_memory_indicator_reports_personalized_count_and_opt_out() -> None:
    request = _request(enabled=True)
    actor = _actor()
    project_id = uuid4()
    repo = request.app.state.editorial_learning_repository

    # No promoted memory yet.
    indicator = asyncio.run(get_memory_indicator(project_id, request, actor))
    assert indicator.enabled is True
    assert indicator.personalized is False
    assert indicator.opt_out_available is True

    repo.set_policy_if_revision(
        workspace_id=actor.workspace_id,
        user_id=actor.user_id,
        memory_enabled=True,
        training_enabled=True,
        policy_version="editorial-consent/v1",
        retention_days=None,
        expected_revision=None,
    )
    for layer, after in (("music", {"mood": "warm"}), ("effects", {"preset": "film_grain"})):
        repo.capture_delta(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            user_id=actor.user_id,
            source_event_id=uuid4(),
            layer=layer,
            delta={"before": {}, "after": after},
            normalizer_version="editorial-delta/v1",
            memory_eligible=True,
            training_eligible=True,
        )
    dataset = repo.freeze_dataset(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        minimum_examples=1,
    )
    repo.promote(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        dataset_id=dataset.id,
        expected_revision=0,
    )

    personalized = asyncio.run(get_memory_indicator(project_id, request, actor))
    assert personalized.personalized is True
    assert personalized.example_count == 2
    assert personalized.memory_version

    repo.revoke(
        workspace_id=actor.workspace_id,
        user_id=actor.user_id,
        memory=True,
        training=True,
        reason="user_request",
    )
    withdrawn = asyncio.run(get_memory_indicator(project_id, request, actor))
    assert withdrawn.personalized is False


def test_memory_indicator_disabled_feature_reports_not_personalized() -> None:
    request = _request(enabled=False)
    actor = _actor()
    indicator = asyncio.run(get_memory_indicator(uuid4(), request, actor))
    assert indicator.enabled is False
    assert indicator.personalized is False
