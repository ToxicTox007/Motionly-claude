import asyncio
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.plans import _apply_phase5_learning
from app.api.scale import (
    complete_style_reference,
    create_brand_kit,
    create_style_profile,
    delete_brand_kit,
    get_collaboration_manifest,
    init_style_reference,
    list_brand_kits,
    list_project_scale_resources,
    list_saved_templates,
    select_audio_stem,
    train_style_profile,
    update_brand_kit,
    update_collaboration_manifest,
)
from app.repositories.editorial_learning import InMemoryEditorialLearningRepository
from app.repositories.scale import (
    SCALE_RESOURCE_COLUMNS,
    InMemoryScaleRepository,
    ScaleResourceRecord,
    SupabaseScaleRepository,
)
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.plans import PlanCreateRequest
from app.schemas.scale import (
    BrandKitCreateRequest,
    BrandKitSettings,
    BrandKitUpdateRequest,
    CollaborationManifestUpdateRequest,
    ScaleResourceStatus,
    StemSelectionUpdateRequest,
    StyleProfileCreateRequest,
    StyleReferenceCompleteRequest,
    StyleReferenceInitRequest,
    StyleTrainRequest,
)
from app.services.generic_media_upload import GenericMediaUploadSessionService
from app.services.local_storage import LocalObjectStorage
from app.services.model_domain_adapters import register_stem_route
from app.services.model_gateway import ModelGateway, OperationsGatewayTelemetry
from app.services.object_references import ObjectReferenceService
from app.services.operations import OperationsRepository
from app.services.queue import QueueRegistry, ScaleQueue
from app.services.scale import (
    FixtureRunwayProvider,
    FixtureStemSeparator,
    _remove_stem_temp_directory,
    _separate_stems,
    _train_style,
    execute_scale_resource,
)
from app.services.storage import StorageSigner
from app.services.storage_quota import StorageQuotaService


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


def test_supabase_scale_repository_lists_with_explicit_projection(monkeypatch) -> None:
    repository = SupabaseScaleRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: [])

    monkeypatch.setattr("app.repositories.scale.SupabaseRestClient.request", fake_request)

    assert (
        repository.list(
            workspace_id=workspace_id,
            project_id=project_id,
            kind="collaboration_manifest",
            limit=25,
        )
        == []
    )
    assert calls == [
        (
            "GET",
            "/phase5_resources",
            {
                "params": {
                    "workspace_id": f"eq.{workspace_id}",
                    "select": SCALE_RESOURCE_COLUMNS,
                    "order": "updated_at.desc",
                    "limit": "25",
                    "kind": "eq.collaboration_manifest",
                    "project_id": f"eq.{project_id}",
                }
            },
        )
    ]


def test_supabase_scale_async_reads_use_bounded_projections(monkeypatch) -> None:
    repository = SupabaseScaleRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    resource_id = uuid4()
    now = "2026-07-06T00:00:00+00:00"
    row = {
        "id": str(resource_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "kind": "collaboration_manifest",
        "name": "Shared edit manifest",
        "status": "ready",
        "payload": {"manifest": {}},
        "result": {},
        "revision": 1,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    responses = [[], [row]]
    calls = []

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: responses.pop(0))

    monkeypatch.setattr(
        "app.repositories.scale.SupabaseRestClient.async_request",
        fake_async_request,
    )

    listed = asyncio.run(
        repository.list_async(
            workspace_id=workspace_id,
            project_id=project_id,
            kind="collaboration_manifest",
            limit=25,
        )
    )
    record = asyncio.run(repository.get_async(resource_id=resource_id, workspace_id=workspace_id))

    assert listed == []
    assert record.id == resource_id
    assert calls == [
        (
            "GET",
            "/phase5_resources",
            {
                "params": {
                    "workspace_id": f"eq.{workspace_id}",
                    "select": SCALE_RESOURCE_COLUMNS,
                    "order": "updated_at.desc",
                    "limit": "25",
                    "kind": "eq.collaboration_manifest",
                    "project_id": f"eq.{project_id}",
                }
            },
        ),
        (
            "GET",
            "/phase5_resources",
            {
                "params": {
                    "id": f"eq.{resource_id}",
                    "workspace_id": f"eq.{workspace_id}",
                    "select": SCALE_RESOURCE_COLUMNS,
                    "limit": "1",
                }
            },
        ),
    ]


def test_scale_api_prefers_async_repository_methods() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    actor = Actor(user_id=uuid4(), workspace_id=workspace_id)

    class AsyncScaleRepository(InMemoryScaleRepository):
        def __init__(self) -> None:
            super().__init__()
            self.async_calls: list[str] = []

        async def create_async(self, **kwargs):
            self.async_calls.append("create")
            return self.create(**kwargs)

        async def list_async(self, **kwargs):
            self.async_calls.append("list")
            return self.list(**kwargs)

        async def latest_async(self, **kwargs):
            self.async_calls.append("latest")
            return self.latest(**kwargs)

        async def update_if_revision_async(self, **kwargs):
            self.async_calls.append("update-if-revision")
            return self.update_if_revision(**kwargs)

    repository = AsyncScaleRepository()
    repository.create(
        workspace_id=workspace_id,
        project_id=None,
        kind="saved_template",
        name="Template",
        status=ScaleResourceStatus.READY,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                scale_repository=repository,
                project_repository=SimpleNamespace(get_project=lambda **_: object()),
            )
        )
    )

    asyncio.run(
        list_project_scale_resources(
            project_id=project_id,
            actor=actor,
            request=request,
            limit=25,
        )
    )
    asyncio.run(list_saved_templates(actor=actor, request=request, limit=25))
    initial = asyncio.run(
        get_collaboration_manifest(project_id=project_id, actor=actor, request=request)
    )
    asyncio.run(
        update_collaboration_manifest(
            project_id=project_id,
            payload=CollaborationManifestUpdateRequest(
                base_revision=initial.revision,
                manifest={"plan_id": "one"},
            ),
            actor=actor,
            request=request,
        )
    )

    assert repository.async_calls == [
        "list",
        "list",
        "latest",
        "create",
        "latest",
        "update-if-revision",
    ]


def _ready_stem_selection_context(tmp_path: Path):
    workspace_id = uuid4()
    project_id = uuid4()
    actor = Actor(user_id=uuid4(), workspace_id=workspace_id)
    repository = InMemoryScaleRepository()
    storage = LocalObjectStorage(root=tmp_path)
    record = repository.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="stem_separation",
        name="Source audio stems",
        status=ScaleResourceStatus.READY,
        result={},
    )
    vocal_path = f"workspaces/{workspace_id}/projects/{project_id}/stems/{record.id}/vocals.wav"
    no_vocals_path = (
        f"workspaces/{workspace_id}/projects/{project_id}/stems/{record.id}/no_vocals.wav"
    )
    record.result = {
        "provider": "demucs",
        "model": "htdemucs",
        "active": "vocals",
        "vocals": {"bucket": "raw-uploads", "path": vocal_path},
        "no_vocals": {"bucket": "raw-uploads", "path": no_vocals_path},
    }
    storage.write_object(bucket="raw-uploads", path=vocal_path, content=b"vocal")
    storage.write_object(bucket="raw-uploads", path=no_vocals_path, content=b"instrumental")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                project_repository=SimpleNamespace(get_project=lambda **_kwargs: object()),
                scale_repository=repository,
                object_storage=storage,
            )
        )
    )
    return actor, project_id, repository, record, storage, request


def test_select_audio_stem_persists_verified_no_vocals_with_cas(tmp_path: Path) -> None:
    actor, project_id, repository, record, _storage, request = _ready_stem_selection_context(
        tmp_path
    )

    response = asyncio.run(
        select_audio_stem(
            project_id=project_id,
            resource_id=record.id,
            payload=StemSelectionUpdateRequest(active="no_vocals", expected_revision=1),
            actor=actor,
            request=request,
        )
    )

    assert response.revision == 2
    assert response.result["active"] == "no_vocals"
    assert response.result["vocals"] == record.result["vocals"]
    assert response.result["no_vocals"] == record.result["no_vocals"]
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            select_audio_stem(
                project_id=project_id,
                resource_id=record.id,
                payload=StemSelectionUpdateRequest(active="vocals", expected_revision=1),
                actor=actor,
                request=request,
            )
        )
    assert exc_info.value.code == "collaboration_revision_conflict"
    assert repository.get(resource_id=record.id, workspace_id=actor.workspace_id).result[
        "active"
    ] == ("no_vocals")


def test_select_audio_stem_rejects_missing_selected_artifact(tmp_path: Path) -> None:
    actor, project_id, _repository, record, storage, request = _ready_stem_selection_context(
        tmp_path
    )
    selected = record.result["no_vocals"]
    storage.delete_object(bucket=selected["bucket"], path=selected["path"])

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            select_audio_stem(
                project_id=project_id,
                resource_id=record.id,
                payload=StemSelectionUpdateRequest(active="no_vocals", expected_revision=1),
                actor=actor,
                request=request,
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "stem_artifact_unavailable"


def test_list_project_scale_resources_passes_limit_to_repository() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    repository = InMemoryScaleRepository()
    first = repository.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="model_training",
        name="First",
        status=ScaleResourceStatus.READY,
    )
    second = repository.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="model_training",
        name="Second",
        status=ScaleResourceStatus.READY,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                project_repository=SimpleNamespace(get_project=lambda **_kwargs: None),
                scale_repository=repository,
            )
        )
    )

    response = asyncio.run(
        list_project_scale_resources(
            project_id=project_id,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            request=request,
            limit=1,
        )
    )

    assert [resource.id for resource in response.resources] == [second.id]
    assert first.id != second.id


def test_list_saved_templates_passes_limit_to_repository() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    repository = InMemoryScaleRepository()
    first = repository.create(
        workspace_id=workspace_id,
        project_id=None,
        kind="saved_template",
        name="First",
        status=ScaleResourceStatus.READY,
    )
    second = repository.create(
        workspace_id=workspace_id,
        project_id=None,
        kind="saved_template",
        name="Second",
        status=ScaleResourceStatus.READY,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(scale_repository=repository))
    )

    response = asyncio.run(
        list_saved_templates(
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            request=request,
            limit=1,
        )
    )

    assert [resource.id for resource in response.resources] == [second.id]
    assert first.id != second.id


def test_model_training_uses_frozen_consented_dataset_and_promoted_memory() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    learning = InMemoryEditorialLearningRepository()
    learning.set_policy_if_revision(
        workspace_id=workspace_id,
        user_id=user_id,
        memory_enabled=True,
        training_enabled=True,
        policy_version="editorial-consent/v1",
        retention_days=None,
        expected_revision=None,
    )
    learning.capture_delta(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        source_event_id=uuid4(),
        layer="cuts",
        delta={
            "layer": "cuts",
            "outcome": "changed",
            "before": {"pace": "slow"},
            "after": {"pace": "fast"},
        },
        normalizer_version="editorial-delta/v1",
        memory_eligible=True,
        training_eligible=True,
    )
    dataset = learning.freeze_dataset(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        minimum_examples=1,
    )
    scale = InMemoryScaleRepository()
    training = scale.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="model_training",
        name="Preference adapter",
        status=ScaleResourceStatus.QUEUED,
        payload={
            "minimum_examples": 1,
            "editorial_dataset_id": str(dataset.id),
            "editorial_user_id": str(user_id),
        },
    )

    completed = execute_scale_resource(
        repository=scale,
        resource_id=training.id,
        workspace_id=workspace_id,
        settings=None,
        upload_repository=None,
        broll_repository=None,
        visual_provider=None,
        analytics_repository=None,
        editorial_learning_repository=learning,
        stem_separator=None,
        runway_provider=None,
    )
    learning.promote(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        dataset_id=dataset.id,
        expected_revision=0,
    )
    adapted = _apply_phase5_learning(
        payload=PlanCreateRequest(),
        request=SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    scale_repository=scale,
                    editorial_learning_repository=learning,
                    settings=SimpleNamespace(editorial_learning_enabled=True),
                )
            )
        ),
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
    )

    assert completed.status == ScaleResourceStatus.READY
    assert completed.result["preferences"]["cuts"] == {"pace": "fast"}
    assert completed.result["dataset_id"] == str(dataset.id)
    assert "Consented editorial memory provenance" in adapted.prompt


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


def _stem_context(tmp_path):
    workspace_id = uuid4()
    project_id = uuid4()
    uploads = InMemoryUploadRepository()
    uploads.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = uploads.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="source.wav",
        original_filename="source.wav",
        mime_type="audio/wav",
        file_size_bytes=10,
    )
    uploads.attach_active_video(project_id=project_id, video_id=upload.id)
    upload.source_object_reference_id = uuid4()
    LocalObjectStorage(root=tmp_path).write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"RIFF-audio",
    )
    record = ScaleResourceRecord(
        id=uuid4(),
        workspace_id=workspace_id,
        project_id=project_id,
        kind="stem_separation",
        name="Stems",
        status=ScaleResourceStatus.QUEUED,
        payload={"model": "htdemucs"},
    )
    settings = SimpleNamespace(
        app_env="production",
        media_work_dir=str(tmp_path),
        supabase_url=None,
        supabase_service_role_key=None,
        upload_storage_bucket="raw-uploads",
        demucs_model="htdemucs",
    )
    return uploads, record, settings


def test_production_stems_route_through_gateway_and_persist_provenance(
    monkeypatch, tmp_path
) -> None:
    uploads, record, settings = _stem_context(tmp_path)
    operations = OperationsRepository()
    gateway = ModelGateway(
        adapters={},
        routes=[],
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=None,
            operations_repository=operations,
        ),
    )
    separator = FixtureStemSeparator()
    route = register_stem_route(
        gateway,
        separator=separator,
        model="htdemucs",
        revision="demucs==4.1.0",
    )

    materialized = []

    def fake_materialize(*, upload, storage):
        materialized.append(upload.source_object_reference_id)
        return storage.ensure_object(
            bucket=upload.storage_bucket,
            path=upload.storage_path,
        )

    monkeypatch.setattr("app.services.scale.materialize_upload_source", fake_materialize)

    result = _separate_stems(
        record=record,
        settings=settings,
        upload_repository=uploads,
        stem_separator=separator,
        model_gateway=gateway,
        stem_gateway_route=route,
    )

    assert materialized == [
        uploads.get_active_upload(
            project_id=record.project_id,
            workspace_id=record.workspace_id,
        ).source_object_reference_id
    ]
    assert result["model_gateway"]["route_id"] == route.route_id
    assert result["model_gateway"]["model_revision"] == "demucs==4.1.0"
    assert operations.provider_events[0]["operation"] == "audio.stem_separation"
    assert operations.provider_events[0]["workspace_id"] == record.workspace_id
    assert operations.provider_events[0]["project_id"] == record.project_id


def test_production_stems_fail_closed_before_direct_separator(tmp_path) -> None:
    uploads, record, settings = _stem_context(tmp_path)

    class ForbiddenSeparator:
        def separate(self, **_kwargs):
            raise AssertionError("production stems must not call Demucs directly")

    with pytest.raises(ApiError) as exc_info:
        _separate_stems(
            record=record,
            settings=settings,
            upload_repository=uploads,
            stem_separator=ForbiddenSeparator(),
        )

    assert exc_info.value.code == "stem_gateway_route_required"


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


def test_brand_kit_crud_persists_workspace_defaults_and_validates_references() -> None:
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    actor = Actor(user_id=uuid4(), workspace_id=workspace_id)
    repository = InMemoryScaleRepository()
    profile = repository.create(
        workspace_id=workspace_id,
        project_id=uuid4(),
        kind="style_profile",
        name="Studio profile",
        status=ScaleResourceStatus.READY,
    )
    template = repository.create(
        workspace_id=workspace_id,
        project_id=None,
        kind="saved_template",
        name="Studio template",
        status=ScaleResourceStatus.READY,
    )
    foreign_profile = repository.create(
        workspace_id=other_workspace_id,
        project_id=uuid4(),
        kind="style_profile",
        name="Foreign profile",
        status=ScaleResourceStatus.READY,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(scale_repository=repository))
    )
    payload = BrandKitCreateRequest(
        name="Acme Studio",
        settings=BrandKitSettings(
            colors=["#111111", "#ff5500", "#111111"],
            font_family="Inter",
            style_profile_id=profile.id,
            saved_template_id=template.id,
            creator_template="founder_reel_v1",
            logo_asset={
                "label": "Acme logo",
                "asset_url": "https://cdn.example.com/acme-logo.png",
            },
            tone_rules={"voice": "Confident and clear", "do_rules": ["Lead with proof"]},
            default_export={"platform": "tiktok", "resolution": "1080p"},
        ),
    )

    created = asyncio.run(create_brand_kit(payload=payload, actor=actor, request=request))
    assert created.kind == "brand_kit"
    assert created.payload["settings"]["colors"] == ["#111111", "#ff5500"]
    assert created.payload["settings"]["default_export"]["platform"] == "tiktok"

    listed = asyncio.run(list_brand_kits(actor=actor, request=request))
    assert [item.id for item in listed.resources] == [created.id]

    updated = asyncio.run(
        update_brand_kit(
            brand_kit_id=created.id,
            payload=BrandKitUpdateRequest(
                name="Acme Social",
                settings=payload.settings.model_copy(update={"font_family": "Montserrat"}),
                expected_revision=created.revision,
            ),
            actor=actor,
            request=request,
        )
    )
    assert updated.name == "Acme Social"
    assert updated.revision == created.revision + 1
    assert updated.payload["settings"]["font_family"] == "Montserrat"

    with pytest.raises(ApiError) as error:
        asyncio.run(
            create_brand_kit(
                payload=BrandKitCreateRequest(
                    name="Invalid",
                    settings=BrandKitSettings(style_profile_id=foreign_profile.id),
                ),
                actor=actor,
                request=request,
            )
        )
    assert error.value.code == "workspace_access_denied"

    asyncio.run(delete_brand_kit(brand_kit_id=created.id, actor=actor, request=request))
    assert asyncio.run(list_brand_kits(actor=actor, request=request)).resources == []


def make_style_upload_request(repository: InMemoryScaleRepository) -> SimpleNamespace:
    signer = StorageSigner(base_url="http://localhost:8000")
    storage = LocalObjectStorage(root=tempfile.mkdtemp(prefix="motionly-style-media-"))
    quota = StorageQuotaService(in_memory=True)
    references = ObjectReferenceService(storage_signer=signer, in_memory=True)
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(upload_storage_bucket="raw-uploads"),
                project_repository=SimpleNamespace(get_project=lambda **_: object()),
                scale_repository=repository,
                scale_queue=ScaleQueue(QueueRegistry()),
                storage_signer=signer,
                object_storage=storage,
                storage_quota_service=quota,
                object_reference_service=references,
                media_upload_session_service=GenericMediaUploadSessionService(
                    storage_signer=signer,
                    object_storage=storage,
                    quota_service=quota,
                    object_reference_service=references,
                ),
            )
        )
    )


def write_style_part(
    request: SimpleNamespace,
    *,
    session_id,
    workspace_id,
    project_id,
    content: bytes,
) -> None:
    session = asyncio.run(
        request.app.state.media_upload_session_service.get(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    request.app.state.object_storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
        content=content,
    )


def test_style_reference_compatibility_requires_verified_media_and_training_refs() -> None:
    workspace_id, project_id = uuid4(), uuid4()
    actor = Actor(user_id=uuid4(), workspace_id=workspace_id)
    repository = InMemoryScaleRepository()
    request = make_style_upload_request(repository)
    profile = asyncio.run(
        create_style_profile(
            project_id=project_id,
            payload=StyleProfileCreateRequest(name="Creator style"),
            actor=actor,
            request=request,
        )
    )

    first_content = b"first style sample"
    first = asyncio.run(
        init_style_reference(
            project_id=project_id,
            profile_id=profile.id,
            payload=StyleReferenceInitRequest(
                filename="first.mp4",
                mime_type="video/mp4",
                file_size_bytes=len(first_content),
                sha256_hash=hashlib.sha256(first_content).hexdigest(),
            ),
            actor=actor,
            request=request,
        )
    )
    assert first.session_id
    assert ".mp4.parts/" in first.signed_upload_url

    with pytest.raises(ApiError) as error:
        asyncio.run(
            complete_style_reference(
                project_id=project_id,
                profile_id=profile.id,
                payload=StyleReferenceCompleteRequest(reference_id=first.reference_id),
                actor=actor,
                request=request,
            )
        )
    assert error.value.code == "upload_parts_incomplete"
    assert first.profile.payload["references"][0]["status"] == "uploading"

    write_style_part(
        request,
        session_id=first.session_id,
        workspace_id=workspace_id,
        project_id=project_id,
        content=first_content,
    )
    completed = asyncio.run(
        complete_style_reference(
            project_id=project_id,
            profile_id=profile.id,
            payload=StyleReferenceCompleteRequest(reference_id=first.reference_id),
            actor=actor,
            request=request,
        )
    )

    for index in range(2, 6):
        content = f"style sample {index}".encode()
        initialized = asyncio.run(
            init_style_reference(
                project_id=project_id,
                profile_id=profile.id,
                payload=StyleReferenceInitRequest(
                    filename=f"sample-{index}.mp4",
                    mime_type="video/mp4",
                    file_size_bytes=len(content),
                    sha256_hash=hashlib.sha256(content).hexdigest(),
                ),
                actor=actor,
                request=request,
            )
        )
        write_style_part(
            request,
            session_id=initialized.session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            content=content,
        )
        completed = asyncio.run(
            complete_style_reference(
                project_id=project_id,
                profile_id=profile.id,
                payload=StyleReferenceCompleteRequest(reference_id=initialized.reference_id),
                actor=actor,
                request=request,
            )
        )

    references = completed.payload["references"]
    assert len(references) == 5
    assert all(item["status"] == "verified" for item in references)
    assert all(item["object_reference_id"] for item in references)
    assert all("storage_bucket" not in item and "storage_path" not in item for item in references)

    trained = asyncio.run(
        train_style_profile(
            project_id=project_id,
            profile_id=profile.id,
            payload=StyleTrainRequest(),
            actor=actor,
            request=request,
        )
    )
    assert trained.status == ScaleResourceStatus.QUEUED
    assert len(request.app.state.scale_queue.registry.jobs) == 1

    asyncio.run(
        request.app.state.object_reference_service.mark_deleted(
            workspace_id=workspace_id,
            project_id=project_id,
            reference_id=UUID(str(references[0]["object_reference_id"])),
        )
    )
    with pytest.raises(ApiError) as error:
        asyncio.run(
            train_style_profile(
                project_id=project_id,
                profile_id=profile.id,
                payload=StyleTrainRequest(),
                actor=actor,
                request=request,
            )
        )
    assert error.value.code == "object_reference_not_found"

    with pytest.raises(ApiError) as error:
        asyncio.run(
            init_style_reference(
                project_id=project_id,
                profile_id=profile.id,
                payload=StyleReferenceInitRequest(
                    filename="other.mp4",
                    mime_type="video/mp4",
                    file_size_bytes=1,
                ),
                actor=Actor(user_id=uuid4(), workspace_id=uuid4()),
                request=request,
            )
        )
    assert error.value.code == "workspace_access_denied"


def test_style_training_worker_rechecks_active_durable_references() -> None:
    workspace_id, project_id = uuid4(), uuid4()
    repository = InMemoryScaleRepository()
    profile = repository.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="style_profile",
        name="Worker validation",
        status=ScaleResourceStatus.QUEUED,
        payload={"references": []},
    )
    signer = StorageSigner(base_url="http://localhost:8000")
    references = ObjectReferenceService(storage_signer=signer, in_memory=True)
    digest = hashlib.sha256(b"x").hexdigest()
    payload_references = []
    for _ in range(5):
        stable_id = uuid4()
        durable = asyncio.run(
            references.create_reference(
                workspace_id=workspace_id,
                project_id=project_id,
                storage_bucket="raw-uploads",
                storage_path=(
                    f"workspaces/{workspace_id}/projects/{project_id}/style-references/"
                    f"{profile.id}/{stable_id}.mp4"
                ),
                owner_type="style_profile",
                owner_id=profile.id,
                object_type="style_reference",
                mime_type="video/mp4",
                size_bytes=1,
                sha256=digest,
            )
        )
        payload_references.append(
            {
                "id": str(stable_id),
                "status": "verified",
                "object_reference_id": str(durable.id),
                "verified_size_bytes": 1,
                "verified_sha256": digest,
                # The worker must not trust these legacy-shaped payload values.
                "storage_bucket": "attacker-controlled",
                "storage_path": "attacker-controlled",
            }
        )
    profile.payload["references"] = payload_references
    asyncio.run(
        references.mark_deleted(
            workspace_id=workspace_id,
            project_id=project_id,
            reference_id=UUID(payload_references[0]["object_reference_id"]),
        )
    )

    with pytest.raises(ApiError) as error:
        _train_style(
            record=profile,
            settings=SimpleNamespace(upload_storage_bucket="raw-uploads"),
            visual_provider=None,
            object_reference_service=references,
        )
    assert error.value.code == "object_reference_not_found"


def test_style_multipart_mutations_reject_foreign_sessions_before_side_effects() -> None:
    from app.api.scale import (
        abort_style_reference_multipart,
        confirm_style_reference_multipart_part,
    )
    from app.schemas.media_uploads import PartCompleteRequest

    workspace_id, project_id = uuid4(), uuid4()
    actor = Actor(user_id=uuid4(), workspace_id=workspace_id)
    repository = InMemoryScaleRepository()
    request = make_style_upload_request(repository)
    profile = asyncio.run(
        create_style_profile(
            project_id=project_id,
            payload=StyleProfileCreateRequest(name="Protected style"),
            actor=actor,
            request=request,
        )
    )
    service = request.app.state.media_upload_session_service
    foreign_session = asyncio.run(
        service.initiate(
            workspace_id=workspace_id,
            project_id=project_id,
            owner_type="stock_asset",
            owner_id=uuid4(),
            object_type="broll_asset",
            final_storage_bucket="raw-uploads",
            final_storage_path=(
                f"workspaces/{workspace_id}/projects/{project_id}/broll/{uuid4()}.mp4"
            ),
            filename="foreign.mp4",
            mime_type="video/mp4",
            file_size_bytes=4,
        )
    )

    with pytest.raises(ApiError) as error:
        asyncio.run(
            confirm_style_reference_multipart_part(
                project_id=project_id,
                profile_id=profile.id,
                session_id=foreign_session.id,
                part_number=1,
                payload=PartCompleteRequest(),
                actor=actor,
                request=request,
            )
        )
    assert error.value.code == "media_upload_session_not_found"
    with pytest.raises(ApiError) as error:
        asyncio.run(
            abort_style_reference_multipart(
                project_id=project_id,
                profile_id=profile.id,
                session_id=foreign_session.id,
                actor=actor,
                request=request,
            )
        )
    assert error.value.code == "media_upload_session_not_found"
    untouched = asyncio.run(
        service.inspect(
            session_id=foreign_session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    assert untouched.status.value == "active"
    assert untouched.parts[0].status.value == "pending"


def test_style_reference_delete_uses_generic_media_lifecycle() -> None:
    from app.api.scale import delete_style_reference

    workspace_id, project_id = uuid4(), uuid4()
    actor = Actor(user_id=uuid4(), workspace_id=workspace_id)
    repository = InMemoryScaleRepository()
    request = make_style_upload_request(repository)
    profile = asyncio.run(
        create_style_profile(
            project_id=project_id,
            payload=StyleProfileCreateRequest(name="Disposable style"),
            actor=actor,
            request=request,
        )
    )
    content = b"disposable style sample"
    initialized = asyncio.run(
        init_style_reference(
            project_id=project_id,
            profile_id=profile.id,
            payload=StyleReferenceInitRequest(
                filename="disposable.mp4",
                mime_type="video/mp4",
                file_size_bytes=len(content),
                sha256_hash=hashlib.sha256(content).hexdigest(),
            ),
            actor=actor,
            request=request,
        )
    )
    write_style_part(
        request,
        session_id=initialized.session_id,
        workspace_id=workspace_id,
        project_id=project_id,
        content=content,
    )
    asyncio.run(
        complete_style_reference(
            project_id=project_id,
            profile_id=profile.id,
            payload=StyleReferenceCompleteRequest(reference_id=initialized.reference_id),
            actor=actor,
            request=request,
        )
    )

    deleted = asyncio.run(
        delete_style_reference(
            project_id=project_id,
            profile_id=profile.id,
            reference_id=initialized.reference_id,
            actor=actor,
            request=request,
        )
    )
    assert deleted.payload["references"][0]["status"] == "deleted"
    session = asyncio.run(
        request.app.state.media_upload_session_service.inspect(
            session_id=initialized.session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    assert (
        asyncio.run(
            request.app.state.storage_quota_service.get_reservation_state(
                session.quota_reservation_id
            )
        )
        == "deleted"
    )

    # Repeating a deletion after the first local completion is safe, mirroring the
    # response-loss retry path of the durable database implementation.
    repeated = asyncio.run(
        delete_style_reference(
            project_id=project_id,
            profile_id=profile.id,
            reference_id=initialized.reference_id,
            actor=actor,
            request=request,
        )
    )
    assert repeated.payload["references"][0]["status"] == "deleted"


def test_style_reference_deletion_allows_replacement_and_training() -> None:
    from app.api.scale import delete_style_reference

    workspace_id, project_id = uuid4(), uuid4()
    actor = Actor(user_id=uuid4(), workspace_id=workspace_id)
    repository = InMemoryScaleRepository()
    request = make_style_upload_request(repository)
    profile = asyncio.run(
        create_style_profile(
            project_id=project_id,
            payload=StyleProfileCreateRequest(name="Replaceable style"),
            actor=actor,
            request=request,
        )
    )

    reference_ids = []
    for index in range(5):
        content = f"style sample {index}".encode()
        initialized = asyncio.run(
            init_style_reference(
                project_id=project_id,
                profile_id=profile.id,
                payload=StyleReferenceInitRequest(
                    filename=f"sample-{index}.mp4",
                    mime_type="video/mp4",
                    file_size_bytes=len(content),
                    sha256_hash=hashlib.sha256(content).hexdigest(),
                ),
                actor=actor,
                request=request,
            )
        )
        write_style_part(
            request,
            session_id=initialized.session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            content=content,
        )
        profile = asyncio.run(
            complete_style_reference(
                project_id=project_id,
                profile_id=profile.id,
                payload=StyleReferenceCompleteRequest(reference_id=initialized.reference_id),
                actor=actor,
                request=request,
            )
        )
        reference_ids.append(initialized.reference_id)

    deleted = asyncio.run(
        delete_style_reference(
            project_id=project_id,
            profile_id=profile.id,
            reference_id=reference_ids[0],
            actor=actor,
            request=request,
        )
    )
    assert sum(item["status"] != "deleted" for item in deleted.payload["references"]) == 4

    replacement_content = b"replacement style sample"
    replacement = asyncio.run(
        init_style_reference(
            project_id=project_id,
            profile_id=profile.id,
            payload=StyleReferenceInitRequest(
                filename="replacement.mp4",
                mime_type="video/mp4",
                file_size_bytes=len(replacement_content),
                sha256_hash=hashlib.sha256(replacement_content).hexdigest(),
            ),
            actor=actor,
            request=request,
        )
    )
    assert len(replacement.profile.payload["references"]) == 6
    assert (
        sum(item["status"] != "deleted" for item in replacement.profile.payload["references"]) == 5
    )

    write_style_part(
        request,
        session_id=replacement.session_id,
        workspace_id=workspace_id,
        project_id=project_id,
        content=replacement_content,
    )
    completed = asyncio.run(
        complete_style_reference(
            project_id=project_id,
            profile_id=profile.id,
            payload=StyleReferenceCompleteRequest(reference_id=replacement.reference_id),
            actor=actor,
            request=request,
        )
    )
    assert sum(item["status"] == "verified" for item in completed.payload["references"]) == 5

    trained = asyncio.run(
        train_style_profile(
            project_id=project_id,
            profile_id=profile.id,
            payload=StyleTrainRequest(),
            actor=actor,
            request=request,
        )
    )
    assert trained.status == ScaleResourceStatus.QUEUED
