from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx

from app.repositories.billing import InMemoryBillingRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.renders import InMemoryRenderRepository, SupabaseRenderRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.repositories.visual import InMemoryVisualRepository
from app.schemas.billing import UsageEventType
from app.schemas.captions import LanguageHint, TranscriptStatus
from app.schemas.renders import RenderOutputFormat
from app.schemas.uploads import UploadStatus, VideoMetadata
from app.schemas.visual import VisualManifestStatus
from app.services.queue import (
    BrollQueue,
    EffectsQueue,
    MatteQueue,
    MusicQueue,
    PlanQueue,
    PreprocessQueue,
    PublishQueue,
    QueueJobStatus,
    QueueRegistry,
    RenderQueue,
    TranscriptionQueue,
    VisualQueue,
)
from app.services.retention import RetentionService
from app.services.transcription_jobs import run_transcription_for_upload
from app.services.upload_jobs import complete_upload_preprocessing
from app.services.visual_jobs import build_visual_cache_key, queue_visual_analysis_for_upload


def _seed_ready_upload():
    upload_repository = InMemoryUploadRepository()
    caption_repository = InMemoryCaptionRepository(upload_repository)
    visual_repository = InMemoryVisualRepository(upload_repository)
    workspace_id = uuid4()
    project_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = upload_repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=100,
    )
    upload.status = UploadStatus.READY
    upload.metadata = VideoMetadata(
        duration_ms=6_000,
        width=1920,
        height=1080,
        fps=30,
        codec="h264",
        has_audio=True,
    )
    upload_repository.attach_active_video(project_id=project_id, video_id=upload.id)
    return upload_repository, caption_repository, visual_repository, upload


def test_phase8_queue_job_ids_are_deterministic_across_job_types() -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    resource_id = uuid4()
    matte_id = uuid4()
    registry = QueueRegistry()

    preprocess = PreprocessQueue(registry)
    transcription = TranscriptionQueue(registry)
    visual = VisualQueue(registry)
    matte = MatteQueue(registry)
    plan = PlanQueue(registry)
    broll = BrollQueue(registry)
    music = MusicQueue(registry)
    effects = EffectsQueue(registry)
    render = RenderQueue(registry)
    publish = PublishQueue(registry)

    jobs = [
        preprocess.enqueue_video_preprocess(
            video_id=resource_id,
            project_id=project_id,
            workspace_id=workspace_id,
        ),
        preprocess.enqueue_video_preprocess(
            video_id=resource_id,
            project_id=project_id,
            workspace_id=workspace_id,
        ),
        transcription.enqueue_transcription(
            project_id=project_id,
            workspace_id=workspace_id,
            video_id=resource_id,
        ),
        transcription.enqueue_caption_generation(
            project_id=project_id,
            workspace_id=workspace_id,
            transcript_id=resource_id,
        ),
        visual.enqueue_visual_analysis(
            project_id=project_id,
            workspace_id=workspace_id,
            video_id=resource_id,
        ),
        matte.enqueue_matte(
            project_id=project_id,
            workspace_id=workspace_id,
            video_id=resource_id,
            matte_id=matte_id,
        ),
        plan.enqueue_plan(
            project_id=project_id,
            workspace_id=workspace_id,
            transcript_id=resource_id,
        ),
        broll.enqueue_broll(
            project_id=project_id,
            workspace_id=workspace_id,
            edit_plan_id=resource_id,
        ),
        music.enqueue_music_selection(
            project_id=project_id,
            workspace_id=workspace_id,
            edit_plan_id=resource_id,
        ),
        effects.enqueue_effects_config(
            project_id=project_id,
            workspace_id=workspace_id,
            edit_plan_id=resource_id,
        ),
        render.enqueue_render(
            render_job_id=resource_id,
            project_id=project_id,
            workspace_id=workspace_id,
        ),
        render.enqueue_render(
            render_job_id=resource_id,
            project_id=project_id,
            workspace_id=workspace_id,
            render_engine="remotion_premium",
        ),
        publish.enqueue_publish(
            publish_job_id=resource_id,
            project_id=project_id,
            workspace_id=workspace_id,
        ),
    ]

    assert jobs[0].id == jobs[1].id
    assert jobs[10].id == jobs[11].id
    assert jobs[11].queue_name == "render_remotion"
    assert len(registry.jobs) == 11


def test_phase8_ready_preprocess_and_transcription_paths_are_idempotent(
    monkeypatch,
    tmp_path,
) -> None:
    upload_repository, caption_repository, _visual_repository, upload = _seed_ready_upload()
    transcript = caption_repository.create_transcript(
        workspace_id=upload.workspace_id,
        project_id=upload.project_id,
        uploaded_video_id=upload.id,
        status=TranscriptStatus.READY,
        language="en",
        language_hint=LanguageHint.AUTO,
        provider="fixture",
        model="fixture",
        words=[],
        confidence=0.99,
        duration_ms=upload.metadata.duration_ms,
        raw_payload={},
        error_code=None,
        error_message=None,
    )
    monkeypatch.setattr(
        "app.services.upload_jobs.create_object_storage",
        lambda **_kwargs: SimpleNamespace(ensure_object=lambda **_k: Path(__file__)),
    )
    monkeypatch.setattr(
        "app.services.upload_jobs.VideoPreprocessor.process",
        lambda self, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not preprocess again")
        ),
    )

    processed = complete_upload_preprocessing(
        upload=upload,
        repository=upload_repository,
        media_work_dir=str(tmp_path),
        supabase_url=None,
        service_role_key=None,
    )

    returned = run_transcription_for_upload(
        upload_repository=upload_repository,
        caption_repository=caption_repository,
        transcription_provider=SimpleNamespace(provider_name="fixture", model="fixture"),
        audio_ml_providers=None,
        workspace_id=upload.workspace_id,
        project_id=upload.project_id,
        video_id=upload.id,
        media_work_dir=str(tmp_path),
        supabase_url=None,
        service_role_key=None,
        retain_debug_audio=False,
    )

    assert processed.id == upload.id
    assert returned.id == transcript.id


def test_phase8_visual_duplicate_queued_work_is_collapsed() -> None:
    upload_repository, _caption_repository, visual_repository, upload = _seed_ready_upload()
    cache_key = build_visual_cache_key(
        upload=upload,
        provider="local_ml",
        model="clip|blip|yolo",
        sample_rate_fps=2.0,
        scene_threshold=0.42,
    )
    existing = visual_repository.create_manifest(
        workspace_id=upload.workspace_id,
        project_id=upload.project_id,
        uploaded_video_id=upload.id,
        status=VisualManifestStatus.QUEUED,
        provider="local_ml",
        model="clip|blip|yolo",
        manifest=None,
        cache_key=cache_key,
        error_code=None,
        error_message=None,
    )

    result = queue_visual_analysis_for_upload(
        upload=upload,
        repository=visual_repository,
        visual_queue=VisualQueue(QueueRegistry()),
        provider="local_ml",
        model="clip|blip|yolo",
        cache_key=cache_key,
    )

    assert result.manifest.id == existing.id
    assert result.job is None
    assert result.cached is True


def test_phase8_recover_stuck_jobs_requeues_then_dead_letters() -> None:
    registry = QueueRegistry()
    workspace_id = uuid4()
    project_id = uuid4()
    render_job = registry.enqueue(
        job_id="render-1",
        kind="render",
        queue_name="render",
        workspace_id=workspace_id,
        project_id=project_id,
        resource_id=uuid4(),
        max_attempts=3,
    )
    dead_letter_job = registry.enqueue(
        job_id="render-2",
        kind="render",
        queue_name="render",
        workspace_id=workspace_id,
        project_id=project_id,
        resource_id=uuid4(),
        max_attempts=1,
    )
    registry.start(render_job.id)
    registry.start(dead_letter_job.id)
    stale_time = datetime.now(UTC) - timedelta(minutes=5)
    registry.jobs[render_job.id] = registry.jobs[render_job.id].__class__(
        **{**registry.jobs[render_job.id].__dict__, "enqueued_at": stale_time}
    )
    registry.jobs[dead_letter_job.id] = registry.jobs[dead_letter_job.id].__class__(
        **{**registry.jobs[dead_letter_job.id].__dict__, "enqueued_at": stale_time}
    )

    recovered = registry.recover_stuck_jobs(timeout_seconds=30, backoff_seconds=5)

    assert {job.id for job in recovered} == {"render-1", "render-2"}
    assert registry.jobs["render-1"].status == QueueJobStatus.QUEUED
    assert registry.jobs["render-1"].error_code == "stuck_timeout"
    assert registry.jobs["render-2"].status == QueueJobStatus.DEAD_LETTER


def test_phase8_render_export_reuse_and_usage_deduplication(monkeypatch) -> None:
    upload_repository = InMemoryUploadRepository()
    caption_repository = InMemoryCaptionRepository(upload_repository)
    render_repository = InMemoryRenderRepository(caption_repository)
    billing_repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    project_id = uuid4()
    render_job_id = uuid4()

    first = render_repository.create_export(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=render_job_id,
        storage_bucket="exports",
        storage_path="video.mp4",
        format=RenderOutputFormat.MP4,
        width=1920,
        height=1080,
        fps=30,
        duration_ms=1000,
        file_size_bytes=100,
        settings_summary={},
    )
    second = render_repository.create_export(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=render_job_id,
        storage_bucket="exports",
        storage_path="video-2.mp4",
        format=RenderOutputFormat.MP4,
        width=1920,
        height=1080,
        fps=30,
        duration_ms=1000,
        file_size_bytes=100,
        settings_summary={},
    )
    first_usage = billing_repository.record_usage(
        workspace_id=workspace_id,
        event_type=UsageEventType.RENDER_SUCCEEDED,
        quantity=1,
        source_id=render_job_id,
    )
    second_usage = billing_repository.record_usage(
        workspace_id=workspace_id,
        event_type=UsageEventType.RENDER_SUCCEEDED,
        quantity=1,
        source_id=render_job_id,
    )

    assert first.id == second.id
    assert second.storage_path == "video-2.mp4"
    assert first_usage.id == second_usage.id


def test_phase8_supabase_export_create_reuses_existing_row(monkeypatch) -> None:
    calls = []
    workspace_id = uuid4()
    project_id = uuid4()
    render_job_id = uuid4()
    export_id = uuid4()

    def fake_request(method, url, *, headers, params=None, json=None, timeout):
        calls.append((method, url, params, json))
        assert method == "POST"
        assert url.endswith("/rpc/motionly_upsert_base_export")
        return httpx.Response(
            200,
            json=[
                {
                    "id": str(export_id),
                    "workspace_id": str(workspace_id),
                    "project_id": str(project_id),
                    "render_job_id": str(render_job_id),
                    "workflow_run_id": None,
                    "source_plan_revision": None,
                    "version": 1,
                    "storage_bucket": json["p_storage_bucket"],
                    "storage_path": json["p_storage_path"],
                    "format": json["p_format"],
                    "width": json["p_width"],
                    "height": json["p_height"],
                    "fps": json["p_fps"],
                    "duration_ms": json["p_duration_ms"],
                    "file_size_bytes": json["p_file_size_bytes"],
                    "thumbnail_url": json["p_thumbnail_url"],
                    "settings_summary": json["p_settings_summary"],
                    "restored_from_export_id": None,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            ],
        )

    monkeypatch.setattr("app.services.supabase.httpx.request", fake_request)

    upload_repository = InMemoryUploadRepository()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    repository = SupabaseRenderRepository(
        caption_repository=InMemoryCaptionRepository(upload_repository),
        supabase_url="https://supabase.example",
        service_role_key="service-role",
    )

    export = repository.create_export(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=render_job_id,
        storage_bucket="exports",
        storage_path="video-2.mp4",
        format=RenderOutputFormat.MP4,
        width=1920,
        height=1080,
        fps=30,
        duration_ms=1000,
        file_size_bytes=100,
        settings_summary={},
    )

    assert export.id == export_id
    assert export.storage_path == "video-2.mp4"
    assert len(calls) == 1
    assert calls[0][3]["p_render_job_id"] == str(render_job_id)


def test_phase8_retention_reconciles_committed_quota_after_object_delete(
    monkeypatch,
    tmp_path,
) -> None:
    calls = []
    reservation_id = "66000000-0000-4000-8000-000000000099"

    def fake_request(method, url, headers, timeout, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/rpc/motionly_claim_storage_deletion_queue"):
            assert kwargs["json"]["p_lease_owner"].startswith("retention:")
            assert kwargs["json"]["p_before"] is None
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "queue-1",
                        "storage_bucket": "raw-uploads",
                        "storage_path": "workspaces/w/projects/p/raw/source.mp4",
                        "quota_reservation_id": reservation_id,
                        "lease_token": "lease-1",
                    }
                ],
            )
        if method == "POST" and url.endswith("/rpc/motionly_delete_committed_storage_quota"):
            return httpx.Response(200, json=True)
        if method == "POST" and url.endswith("/rpc/motionly_ack_storage_deletion_queue"):
            return httpx.Response(200, json=True)
        return httpx.Response(200, json=[])

    deleted = []
    monkeypatch.setattr("app.services.retention.httpx.request", fake_request)
    service = RetentionService(
        settings=SimpleNamespace(
            supabase_url="https://supabase.example",
            supabase_service_role_key="service-role",
            retention_export_days=90,
            retention_local_cache_days=7,
            media_cache_max_bytes=1024,
            media_work_dir=str(tmp_path),
        ),
        storage_signer=SimpleNamespace(delete_object=lambda **kwargs: deleted.append(kwargs)),
    )

    assert service._drain_storage_deletion_queue(minimum_age_days=None) == 1
    assert deleted == [
        {
            "bucket": "raw-uploads",
            "path": "workspaces/w/projects/p/raw/source.mp4",
        }
    ]
    quota_call = next(
        call for call in calls if call[1].endswith("/rpc/motionly_delete_committed_storage_quota")
    )
    assert quota_call[2]["json"] == {"p_reservation_id": reservation_id}
    assert calls[-1][0] == "POST"
    assert calls[-1][1].endswith("/rpc/motionly_ack_storage_deletion_queue")
    assert calls[-1][2]["json"] == {"p_queue_id": "queue-1", "p_lease_token": "lease-1"}


def test_phase8_retention_run_scheduled_is_safe_to_rerun(monkeypatch, tmp_path) -> None:
    calls = []
    run_count = 0

    def fake_request(method, url, headers, timeout, **kwargs):
        nonlocal run_count
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/retention_runs"):
            run_count += 1
            return httpx.Response(200, json=[{"id": f"run-{run_count}"}])
        if method == "POST" and url.endswith("/rpc/motionly_expire_upload_sessions"):
            return httpx.Response(200, json=[0])
        if method == "POST" and url.endswith("/rpc/motionly_expire_storage_quota_reservations"):
            return httpx.Response(
                200,
                json=[{"motionly_expire_storage_quota_reservations": 0}],
            )
        return httpx.Response(200, json=[])

    monkeypatch.setattr("app.services.retention.httpx.request", fake_request)
    service = RetentionService(
        settings=SimpleNamespace(
            supabase_url="https://supabase.example",
            supabase_service_role_key="service-role",
            retention_export_days=90,
            retention_local_cache_days=7,
            media_cache_max_bytes=1024,
            media_work_dir=str(tmp_path),
        ),
        storage_signer=SimpleNamespace(delete_object=lambda **_kwargs: None),
    )

    first = service.run_scheduled()
    second = service.run_scheduled()

    assert first["exports"] == 0
    assert first["expired_storage_reservations"] == 0
    assert first["expired_generic_media_upload_sessions"] == 0
    assert second["exports"] == 0
    assert second["expired_storage_reservations"] == 0
    assert second["expired_generic_media_upload_sessions"] == 0
    retention_starts = [
        call for call in calls if call[0] == "POST" and call[1].endswith("/retention_runs")
    ]
    generic_media_expiries = [
        call
        for call in calls
        if call[0] == "POST"
        and call[1].endswith("/rpc/motionly_expire_generic_media_upload_sessions")
    ]
    reservation_expiries = [
        call
        for call in calls
        if call[0] == "POST" and call[1].endswith("/rpc/motionly_expire_storage_quota_reservations")
    ]
    prompt_cleanup_calls = [
        call
        for call in calls
        if call[0] == "POST"
        and call[1].endswith("/rpc/motionly_claim_storage_deletion_queue")
        and call[2].get("json", {}).get("p_source_table")
        in {
            "upload_session_parts",
            "upload_sessions",
            "generic_media_upload_session_parts",
            "generic_media_upload_sessions",
            "generic_media_deletion",
            "legacy_upload_expiry",
            "legacy_upload_cancellation",
        }
    ]
    assert {call[2]["json"]["p_source_table"] for call in prompt_cleanup_calls} == {
        "upload_session_parts",
        "upload_sessions",
        "generic_media_upload_session_parts",
        "generic_media_upload_sessions",
        "generic_media_deletion",
        "legacy_upload_expiry",
        "legacy_upload_cancellation",
    }
    assert all(call[2]["json"].get("p_before") for call in prompt_cleanup_calls)

    assert len(retention_starts) == 2
    assert len(generic_media_expiries) == 2
    assert len(reservation_expiries) == 2
    # Task 6 expiry stays inside atomic database RPCs, not Python row loops.
    assert not any(
        call[0] == "row"
        and call[1] in ("uploaded_videos", "transcripts", "phase5_resources", "stock_assets")
        for call in calls
    )


def test_retention_acknowledges_generic_media_only_after_storage_delete(
    monkeypatch, tmp_path
) -> None:
    calls = []
    deleted = []

    def fake_request(method, url, headers, timeout, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/rpc/motionly_claim_storage_deletion_queue"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "generic-queue-1",
                        "storage_bucket": "raw-uploads",
                        "storage_path": "workspaces/w/projects/p/broll/final.mp4",
                        "quota_reservation_id": "66000000-0000-4000-8000-000000000123",
                        "generic_media_deletion_job_id": "77000000-0000-4000-8000-000000000123",
                        "lease_token": "lease-generic-1",
                    }
                ],
            )
        if method == "POST" and url.endswith("/rpc/motionly_ack_generic_media_deletion_object"):
            assert deleted == [
                {
                    "bucket": "raw-uploads",
                    "path": "workspaces/w/projects/p/broll/final.mp4",
                }
            ]
            return httpx.Response(200, json=True)
        if method == "POST" and url.endswith("/rpc/motionly_delete_committed_storage_quota"):
            raise AssertionError("generic-media quota must be reconciled by its atomic ack RPC")
        if method == "POST" and url.endswith("/rpc/motionly_ack_storage_deletion_queue"):
            raise AssertionError("generic-media queue rows use the dedicated ack RPC")
        return httpx.Response(200, json=[])

    monkeypatch.setattr("app.services.retention.httpx.request", fake_request)
    service = RetentionService(
        settings=SimpleNamespace(
            supabase_url="https://supabase.example",
            supabase_service_role_key="service-role",
            retention_export_days=90,
            retention_local_cache_days=7,
            media_cache_max_bytes=1024,
            media_work_dir=str(tmp_path),
        ),
        storage_signer=SimpleNamespace(delete_object=lambda **kwargs: deleted.append(kwargs)),
    )

    assert service._drain_storage_deletion_queue(minimum_age_days=None) == 1
    assert any(
        call[1].endswith("/rpc/motionly_ack_generic_media_deletion_object") for call in calls
    )


def test_retention_continues_after_one_storage_delete_failure(monkeypatch, tmp_path) -> None:
    acknowledgements = []

    def fake_request(method, url, headers, timeout, **kwargs):
        if method == "POST" and url.endswith("/rpc/motionly_claim_storage_deletion_queue"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "blocked-queue",
                        "storage_bucket": "raw-uploads",
                        "storage_path": "blocked.mp4",
                        "quota_reservation_id": None,
                        "lease_token": "lease-blocked",
                    },
                    {
                        "id": "healthy-queue",
                        "storage_bucket": "raw-uploads",
                        "storage_path": "healthy.mp4",
                        "quota_reservation_id": None,
                        "lease_token": "lease-healthy",
                    },
                ],
            )
        if method == "POST" and url.endswith("/rpc/motionly_ack_storage_deletion_queue"):
            acknowledgements.append(kwargs["json"])
            return httpx.Response(200, json=True)
        return httpx.Response(200, json=[])

    def delete_object(*, bucket, path):
        del bucket
        if path == "blocked.mp4":
            raise OSError("storage offline")

    monkeypatch.setattr("app.services.retention.httpx.request", fake_request)
    service = RetentionService(
        settings=SimpleNamespace(
            supabase_url="https://supabase.example",
            supabase_service_role_key="service-role",
            retention_export_days=90,
            retention_local_cache_days=7,
            media_cache_max_bytes=1024,
            media_work_dir=str(tmp_path),
        ),
        storage_signer=SimpleNamespace(delete_object=delete_object),
    )

    assert service._drain_storage_deletion_queue(minimum_age_days=None) == 1
    assert acknowledgements == [{"p_queue_id": "healthy-queue", "p_lease_token": "lease-healthy"}]
