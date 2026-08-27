from __future__ import annotations

from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.schemas.renders import RenderJobStatus
from app.services.render_jobs import complete_remotion_render_job
from tests.test_renders import make_render_repository


def _job(repository, project_id, workspace_id, *, status=RenderJobStatus.RENDERING):
    return repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        caption_track_id=repository.latest_caption_track(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        status=status,
        planner_version="cas-test",
        manifest={},
        keep_segments=[],
        progress_pct=88,
    )


def _complete(job, repository):
    return complete_remotion_render_job(
        job=job,
        repository=repository,
        export_bucket="exports",
        export_storage_path=(
            f"workspaces/{job.workspace_id}/projects/{job.project_id}/exports/{job.id}"
            "/attempts/q-test-a-1/final.mp4"
        ),
        benchmark_ms=321,
        file_size_bytes=2048,
        width=1080,
        height=1920,
        fps=30,
        duration_ms=1000,
    )


def test_late_completion_cannot_override_authoritative_cancellation() -> None:
    repository, project_id, workspace_id, _ = make_render_repository()
    job = _job(repository, project_id, workspace_id)
    repository.cancel_render_job_by_id(render_job_id=job.id)

    with pytest.raises(ApiError) as exc_info:
        _complete(job, repository)

    assert exc_info.value.code == "render_terminal_state"
    current = repository.get_render_job_by_id(render_job_id=job.id)
    assert current.status == RenderJobStatus.CANCELED
    assert not [export for export in repository.exports.values() if export.render_job_id == job.id]


def test_completion_cas_is_idempotent_only_for_identical_canonical_output() -> None:
    repository, project_id, workspace_id, _ = make_render_repository()
    job = _job(repository, project_id, workspace_id)

    first_job, first_export = _complete(job, repository)
    replay_job, replay_export = _complete(job, repository)

    assert first_job.status == replay_job.status == RenderJobStatus.COMPLETE
    assert first_export.id == replay_export.id
    complete_events = [
        event for event in repository.events_for_job(job.id) if event.event_type == "complete"
    ]
    assert len(complete_events) == 1

    with pytest.raises(ApiError) as exc_info:
        repository.complete_render(
            workspace_id=workspace_id,
            project_id=project_id,
            render_job_id=job.id,
            storage_bucket="exports",
            storage_path=(
                f"workspaces/{workspace_id}/projects/{project_id}/exports/{job.id}"
                "/attempts/q-loser-a-2/final.mp4"
            ),
            format="mp4",
            width=1080,
            height=1920,
            fps=30,
            duration_ms=1000,
            file_size_bytes=2048,
            settings_summary={
                "render_engine": "remotion_premium",
                "renderer_version": "remotion-timeline-v2",
            },
            benchmark_ms=321,
        )
    assert exc_info.value.code == "render_completion_idempotency_conflict"


def test_failed_terminal_and_output_specs_mismatch_are_not_completable() -> None:
    repository, project_id, workspace_id, _ = make_render_repository()
    failed = _job(repository, project_id, workspace_id, status=RenderJobStatus.FAILED)
    with pytest.raises(ApiError) as failed_error:
        _complete(failed, repository)
    assert failed_error.value.code == "render_terminal_state"

    rendering = _job(repository, project_id, workspace_id)
    with pytest.raises(ApiError) as specs_error:
        complete_remotion_render_job(
            job=rendering,
            repository=repository,
            export_bucket="exports",
            export_storage_path=f"workspaces/{workspace_id}/projects/{project_id}/exports/{uuid4()}.mp4",
            benchmark_ms=1,
            file_size_bytes=1,
            width=720,
            height=1920,
            fps=30,
            duration_ms=1000,
        )
    assert specs_error.value.code == "render_output_specs_mismatch"


def test_supabase_repository_completes_through_cas_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_renders import (
        _export_row,
        _render_job_row,
        _supabase_response,
        make_supabase_render_repository,
    )

    repository = make_supabase_render_repository()
    workspace_id = uuid4()
    project_id = uuid4()
    render_job_id = uuid4()
    export_id = uuid4()
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_request(_self, method: str, path: str, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/rpc/motionly_complete_render":
            return _supabase_response(
                [
                    {
                        "render_job_id": str(render_job_id),
                        "export_id": str(export_id),
                        "status": "complete",
                        "artifact_path": kwargs["json"]["p_storage_path"],
                        "benchmark_ms": 321,
                    }
                ]
            )
        if path == "/render_jobs":
            row = _render_job_row(render_job_id, project_id, workspace_id)
            row.update(
                {
                    "status": "complete",
                    "progress_pct": 100,
                    "artifact_path": kwargs.get("params", {}).get(
                        "artifact_path", "attempt/final.mp4"
                    ),
                    "benchmark_ms": 321,
                }
            )
            return _supabase_response([row])
        if path == "/exports":
            row = _export_row(export_id, render_job_id, project_id, workspace_id)
            row.update(
                {
                    "storage_path": "attempt/final.mp4",
                    "width": 1080,
                    "height": 1920,
                    "file_size_bytes": 2048,
                    "settings_summary": {"render_engine": "remotion_premium"},
                }
            )
            return _supabase_response([row])
        raise AssertionError(f"unexpected Supabase call: {method} {path}")

    monkeypatch.setattr("app.repositories.renders.SupabaseRestClient.request", fake_request)
    completed, exported = repository.complete_render(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=render_job_id,
        storage_bucket="exports",
        storage_path="attempt/final.mp4",
        format="mp4",
        width=1080,
        height=1920,
        fps=30,
        duration_ms=1000,
        file_size_bytes=2048,
        settings_summary={"render_engine": "remotion_premium"},
        benchmark_ms=321,
    )

    assert completed.status == RenderJobStatus.COMPLETE
    assert exported.id == export_id
    assert [path for _, path, _ in calls] == [
        "/rpc/motionly_complete_render",
        "/render_jobs",
        "/exports",
    ]
    assert calls[0][2]["json"]["p_render_job_id"] == str(render_job_id)
    assert calls[0][2]["json"]["p_storage_path"] == "attempt/final.mp4"


def test_concurrent_worker_completion_observes_cancellation_cas() -> None:
    from threading import Barrier, Event, Thread

    repository, project_id, workspace_id, _ = make_render_repository()
    job = _job(repository, project_id, workspace_id)
    start = Barrier(2)
    cancellation_committed = Event()
    errors: list[ApiError] = []

    def cancel() -> None:
        start.wait()
        repository.cancel_render_job_by_id(render_job_id=job.id)
        cancellation_committed.set()

    def complete() -> None:
        start.wait()
        assert cancellation_committed.wait(timeout=2)
        try:
            _complete(job, repository)
        except ApiError as error:
            errors.append(error)

    cancel_thread = Thread(target=cancel)
    complete_thread = Thread(target=complete)
    cancel_thread.start()
    complete_thread.start()
    cancel_thread.join(timeout=2)
    complete_thread.join(timeout=2)

    assert not cancel_thread.is_alive()
    assert not complete_thread.is_alive()
    assert [error.code for error in errors] == ["render_terminal_state"]
    assert repository.get_render_job_by_id(render_job_id=job.id).status == RenderJobStatus.CANCELED
