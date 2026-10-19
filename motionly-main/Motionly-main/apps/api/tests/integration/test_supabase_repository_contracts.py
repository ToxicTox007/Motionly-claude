import os
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.errors import ApiError
from app.repositories.projects import SupabaseProjectRepository
from app.repositories.scale import SupabaseScaleRepository
from app.repositories.uploads import SupabaseUploadRepository
from app.repositories.workflows import SupabaseWorkflowRepository
from app.schemas.projects import ProjectStatus
from app.schemas.scale import ScaleResourceStatus
from app.schemas.uploads import UploadStatus
from app.services.operations import OperationsRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SUPABASE_CONTRACTS") != "1",
    reason="Set RUN_SUPABASE_CONTRACTS=1 against an isolated local Supabase stack.",
)


@pytest.fixture
def supabase_contract_context():
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {"apikey": key, "authorization": f"Bearer {key}"}
    email = f"contract-{uuid4()}@motionly.test"
    created = httpx.post(
        f"{url}/auth/v1/admin/users",
        headers={**headers, "content-type": "application/json"},
        json={"email": email, "password": f"Contract-{uuid4()}!", "email_confirm": True},
        timeout=20,
    )
    created.raise_for_status()
    user_id = UUID(created.json()["id"])
    membership = httpx.get(
        f"{url}/rest/v1/workspace_members",
        headers=headers,
        params={"user_id": f"eq.{user_id}", "select": "workspace_id", "limit": "1"},
        timeout=20,
    )
    membership.raise_for_status()
    workspace_id = UUID(membership.json()[0]["workspace_id"])
    try:
        yield url, key, user_id, workspace_id
    finally:
        httpx.delete(f"{url}/auth/v1/admin/users/{user_id}", headers=headers, timeout=20)


def test_project_upload_and_scale_repositories_round_trip(supabase_contract_context) -> None:
    url, key, user_id, workspace_id = supabase_contract_context
    projects = SupabaseProjectRepository(supabase_url=url, service_role_key=key)
    uploads = SupabaseUploadRepository(supabase_url=url, service_role_key=key)
    scale = SupabaseScaleRepository(supabase_url=url, service_role_key=key)

    project = projects.create_project(
        workspace_id=workspace_id, created_by=user_id, title="Repository contract"
    )
    assert projects.get_project(project_id=project.id, workspace_id=workspace_id).id == project.id
    assert project.id in {item.id for item in projects.list_projects(workspace_id=workspace_id)}
    updated = projects.update_project(
        project_id=project.id,
        workspace_id=workspace_id,
        title="Updated contract",
        status=ProjectStatus.READY,
    )
    assert updated.title == "Updated contract"

    upload = uploads.create_upload(
        project_id=project.id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path=f"contracts/{uuid4()}.mp4",
        original_filename="contract.mp4",
        mime_type="video/mp4",
        file_size_bytes=1024,
    )
    upload = uploads.transition_upload(upload=upload, status=UploadStatus.UPLOADING)
    upload = uploads.transition_upload(upload=upload, status=UploadStatus.UPLOADED)
    assert uploads.get_upload_for_project(
        project_id=project.id, video_id=upload.id, workspace_id=workspace_id
    ).status == UploadStatus.UPLOADED

    resource = scale.create(
        workspace_id=workspace_id,
        project_id=project.id,
        kind="contract",
        name="Contract resource",
        status=ScaleResourceStatus.QUEUED,
        payload={"version": 1},
    )
    resource = scale.update(
        record=resource,
        status=ScaleResourceStatus.READY,
        result={"ok": True},
        revision=2,
    )
    assert scale.get(resource_id=resource.id, workspace_id=workspace_id).result == {"ok": True}

    with pytest.raises(ApiError):
        projects.get_project(project_id=project.id, workspace_id=uuid4())
    scale.delete(resource_id=resource.id, workspace_id=workspace_id)
    projects.delete_project(project_id=project.id, workspace_id=workspace_id)


def test_projects_with_workflow_history_can_be_deleted(supabase_contract_context) -> None:
    """Workflow history is append-only, but deleting its project removes it."""
    url, key, user_id, workspace_id = supabase_contract_context
    headers = {"apikey": key, "authorization": f"Bearer {key}"}
    projects = SupabaseProjectRepository(supabase_url=url, service_role_key=key)
    workflows = SupabaseWorkflowRepository(supabase_url=url, service_role_key=key)
    project = projects.create_project(
        workspace_id=workspace_id, created_by=user_id, title="Delete with history"
    )
    path = f"workspaces/{workspace_id}/projects/{project.id}/raw/source.mp4"

    def rest(method: str, table: str, *, prefer: str | None = None, **kwargs):
        request_headers = {**headers, **({"prefer": prefer} if prefer else {})}
        response = httpx.request(
            method, f"{url}/rest/v1/{table}", headers=request_headers, timeout=20, **kwargs
        )
        response.raise_for_status()
        return response.json() if response.content else None

    upload = rest(
        "POST",
        "uploaded_videos",
        prefer="return=representation",
        json={
            "workspace_id": str(workspace_id),
            "project_id": str(project.id),
            "storage_bucket": "raw-uploads",
            "storage_path": path,
            "original_filename": "source.mp4",
            "mime_type": "video/mp4",
            "byte_size": 2048,
            "file_size_bytes": 2048,
            "status": "processing",
            "workflow_version": "first-draft-v2",
        },
    )[0]
    reference = rest(
        "POST",
        "object_references",
        prefer="return=representation",
        json={
            "workspace_id": str(workspace_id),
            "project_id": str(project.id),
            "storage_bucket": "raw-uploads",
            "storage_path": path,
            "owner_type": "uploaded_video",
            "owner_id": upload["id"],
            "object_type": "source_video",
            "mime_type": "video/mp4",
            "size_bytes": 2048,
            "sha256": "a" * 64,
        },
    )[0]
    rest(
        "PATCH",
        "uploaded_videos",
        params={"id": f"eq.{upload['id']}"},
        json={
            "source_object_reference_id": reference["id"],
            "verified_size_bytes": 2048,
            "verified_sha256": "a" * 64,
        },
    )
    run, _ = workflows.ensure_first_draft_v2(
        workspace_id=workspace_id,
        project_id=project.id,
        uploaded_video_id=UUID(upload["id"]),
        started_by=user_id,
    )
    for table in ("workflow_events", "transactional_outbox"):
        assert rest("GET", table, params={"workflow_run_id": f"eq.{run.id}", "select": "id"})

    projects.delete_project(project_id=project.id, workspace_id=workspace_id)

    for table in ("workflow_runs", "workflow_events", "transactional_outbox", "uploaded_videos"):
        assert rest("GET", table, params={"project_id": f"eq.{project.id}", "select": "id"}) == []



def test_model_gateway_telemetry_round_trips_through_supabase(
    supabase_contract_context,
) -> None:
    url, key, _user_id, workspace_id = supabase_contract_context
    operations = OperationsRepository(supabase_url=url, service_role_key=key)
    request_id = f"task10-contract-{uuid4()}"
    operations.record_provider_slo(
        workspace_id=workspace_id,
        project_id=None,
        provider="azure",
        operation="planning.chat",
        success=True,
        latency_ms=42,
        error_code=None,
        model="planner-deployment",
        model_revision="planner-2026-07-14",
        route_id="azure-planning-primary",
        request_id=request_id,
        fallback_count=1,
        estimated_cost_usd=0.03,
        actual_cost_usd=0.02,
        usage={"input_units": 100, "output_units": 50},
    )

    response = httpx.get(
        f"{url}/rest/v1/provider_slo_events",
        headers={"apikey": key, "authorization": f"Bearer {key}"},
        params={
            "request_id": f"eq.{request_id}",
            "select": (
                "workspace_id,provider,operation,model,model_revision,route_id,request_id,"
                "fallback_count,estimated_cost_usd,actual_cost_usd,usage"
            ),
        },
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()

    assert len(rows) == 1
    assert rows[0] == {
        "workspace_id": str(workspace_id),
        "provider": "azure",
        "operation": "planning.chat",
        "model": "planner-deployment",
        "model_revision": "planner-2026-07-14",
        "route_id": "azure-planning-primary",
        "request_id": request_id,
        "fallback_count": 1,
        "estimated_cost_usd": 0.03,
        "actual_cost_usd": 0.02,
        "usage": {"input_units": 100, "output_units": 50},
    }
