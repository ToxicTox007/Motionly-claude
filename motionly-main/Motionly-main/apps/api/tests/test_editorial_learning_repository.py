from types import SimpleNamespace
from uuid import uuid4

from app.repositories.editorial_learning import SupabaseEditorialLearningRepository


def _policy_row(workspace_id, user_id, *, revision=1, memory=True, training=True):
    return {
        "workspace_id": str(workspace_id),
        "user_id": str(user_id),
        "memory_enabled": memory,
        "training_enabled": training,
        "policy_version": "editorial-consent/v1",
        "revision": revision,
        "retention_days": 90,
        "revoked_at": None,
        "updated_at": "2026-08-09T00:00:00+00:00",
    }


def test_supabase_editorial_repository_uses_atomic_rpcs_and_scoped_reads(monkeypatch) -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    source_event_id = uuid4()
    delta_id = uuid4()
    dataset_id = uuid4()
    now = "2026-08-09T00:00:00+00:00"
    manifest_hash = "a" * 64
    delta_row = {
        "id": str(delta_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "user_id": str(user_id),
        "source_event_id": str(source_event_id),
        "layer": "cuts",
        "normalizer_version": "editorial-delta/v1",
        "delta": {"after": {"pace": "fast"}},
        "content_hash": "b" * 64,
        "policy_revision": 1,
        "memory_eligible": True,
        "training_eligible": True,
        "created_at": now,
        "revoked_at": None,
    }
    dataset_row = {
        "id": str(dataset_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "user_id": str(user_id),
        "status": "frozen",
        "manifest_hash": manifest_hash,
        "created_at": now,
        "revoked_at": None,
    }
    deployment_row = {
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "user_id": str(user_id),
        "active_dataset_id": str(dataset_id),
        "previous_dataset_id": None,
        "state": "active",
        "revision": 1,
        "updated_at": now,
    }
    responses = [
        [_policy_row(workspace_id, user_id)],
        [delta_row],
        [dataset_row],
        [{"delta_id": str(delta_id)}],
        [delta_row],
        [deployment_row],
        [deployment_row],
        [],
        [_policy_row(workspace_id, user_id, revision=2, memory=False, training=False)],
    ]
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: responses.pop(0), text="")

    monkeypatch.setattr(
        "app.repositories.editorial_learning.SupabaseRestClient.request",
        fake_request,
    )
    repository = SupabaseEditorialLearningRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )

    policy = repository.set_policy_if_revision(
        workspace_id=workspace_id,
        user_id=user_id,
        memory_enabled=True,
        training_enabled=True,
        policy_version="editorial-consent/v1",
        retention_days=90,
        expected_revision=0,
    )
    captured = repository.capture_delta(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        source_event_id=source_event_id,
        layer="cuts",
        delta={"after": {"pace": "fast"}},
        normalizer_version="editorial-delta/v1",
        memory_eligible=True,
        training_eligible=True,
    )
    dataset = repository.freeze_dataset(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        minimum_examples=1,
    )
    examples = repository.dataset_deltas(dataset=dataset)
    deployment = repository.promote(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        dataset_id=dataset_id,
        expected_revision=0,
    )
    rolled_back = repository.rollback(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        reason="operator_check",
    )
    revoked = repository.revoke(
        workspace_id=workspace_id,
        user_id=user_id,
        memory=True,
        training=True,
        reason="user_request",
    )

    assert policy.revision == 1
    assert captured is not None and captured.id == delta_id
    assert dataset.item_ids == (delta_id,)
    assert examples == [captured]
    assert deployment.active_dataset_id == dataset_id
    assert rolled_back.revision == 1
    assert revoked.revision == 2
    assert calls[0] == (
        "POST",
        "/rpc/motionly_set_editorial_learning_policy",
        {
            "json": {
                "p_workspace_id": str(workspace_id),
                "p_user_id": str(user_id),
                "p_memory_enabled": True,
                "p_training_enabled": True,
                "p_policy_version": "editorial-consent/v1",
                "p_retention_days": 90,
                "p_expected_revision": 0,
            }
        },
    )
    assert calls[1][1] == "/rpc/motionly_capture_editorial_feedback_delta"
    assert calls[2][1] == "/rpc/motionly_freeze_editorial_training_dataset"
    assert calls[3] == (
        "GET",
        "/editorial_training_dataset_items",
        {
            "params": {
                "dataset_id": f"eq.{dataset_id}",
                "select": "delta_id",
                "order": "delta_id.asc",
                "limit": "10000",
            }
        },
    )
    assert calls[4][2]["params"] == {
        "id": f"in.({delta_id})",
        "workspace_id": f"eq.{workspace_id}",
        "project_id": f"eq.{project_id}",
        "user_id": f"eq.{user_id}",
        "revoked_at": "is.null",
        "select": (
            "id,workspace_id,project_id,user_id,source_event_id,layer,normalizer_version,"
            "delta,content_hash,policy_revision,memory_eligible,training_eligible,created_at,"
            "revoked_at"
        ),
        "limit": "1",
    }
    assert calls[5][1] == "/rpc/motionly_promote_editorial_learning_dataset"
    assert calls[6][1] == "/rpc/motionly_rollback_editorial_learning_deployment"
    assert calls[7][1] == "/rpc/motionly_revoke_editorial_learning"
    assert calls[8][2]["params"]["workspace_id"] == f"eq.{workspace_id}"
    assert calls[8][2]["params"]["user_id"] == f"eq.{user_id}"


def test_supabase_editorial_policy_defaults_deny_and_use_explicit_projection(monkeypatch) -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: [], text="")

    monkeypatch.setattr(
        "app.repositories.editorial_learning.SupabaseRestClient.request",
        fake_request,
    )
    repository = SupabaseEditorialLearningRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        default_retention_days=30,
    )

    policy = repository.get_policy_or_default(workspace_id=workspace_id, user_id=user_id)

    assert policy.memory_enabled is False
    assert policy.training_enabled is False
    assert policy.revision == 0
    assert policy.retention_days == 30
    assert calls == [
        (
            "GET",
            "/editorial_learning_policies",
            {
                "params": {
                    "workspace_id": f"eq.{workspace_id}",
                    "user_id": f"eq.{user_id}",
                    "select": (
                        "workspace_id,user_id,memory_enabled,training_enabled,policy_version,"
                        "revision,retention_days,revoked_at,updated_at"
                    ),
                    "limit": "1",
                }
            },
        )
    ]
