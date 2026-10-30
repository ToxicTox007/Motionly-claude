import hashlib
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.renders import ExportRecord, InMemoryRenderRepository, RenderJobRecord
from app.schemas.renders import RenderJobStatus
from app.services.ffmpeg_render import FFmpegCaptionRenderer
from app.services.local_storage import LocalObjectStorage, create_object_storage
from app.services.media_url_policy import (
    MediaUrlPolicyError,
    decode_bounded_audio_data_url,
    safe_http_stream,
    validate_outbound_media_url,
    validate_render_media_manifest,
)
from app.services.subprocess_env import media_tool_env
from app.services.worker_media import materialize_worker_object


def complete_render_job(
    *,
    job: RenderJobRecord,
    repository: InMemoryRenderRepository,
    metrics_registry: object | None = None,
    export_bucket: str,
    storage_root: str,
    supabase_url: str | None,
    service_role_key: str | None,
    renderer: object | None = None,
    allow_fixture_media: bool = False,
    attempt_id: UUID | None = None,
) -> RenderJobRecord:
    """Run one worker attempt of an FFmpeg render.

    The attempt claims the render first (``attempt_id``, generated per run by
    the BullMQ worker; database audit DB-11) and every later status write is
    conditional on still owning it. If another attempt takes the render over,
    or it is requeued, this attempt stops with 409 ``render_attempt_superseded``
    instead of writing over the newer one.
    """
    # Renders always execute a real CPU render. Tests that need a fast renderer
    # inject one explicitly via `renderer`; no setting can select a stand-in
    # (see apps/api/tests/test_fixture_leakage.py for the leakage assertion).
    if renderer is None:
        renderer = FFmpegCaptionRenderer()
    if job.status == RenderJobStatus.COMPLETE:
        return job
    if job.status == RenderJobStatus.CANCELED:
        raise ApiError(409, "render_cancelled", "Canceled renders cannot be processed.")
    if job.status == RenderJobStatus.FAILED:
        job = repository.transition_job(
            job=job,
            status=RenderJobStatus.QUEUED,
            progress_pct=5,
            message="Render requeued for worker retry.",
            event_type="worker_retry",
            error_code=None,
            error_message=None,
        )

    try:
        job = repository.claim_render_attempt(
            job=job,
            attempt_id=attempt_id or uuid4(),
            progress_pct=10,
            message="Running render preflight.",
            event_type="preflight_started",
        )
    except ApiError as exc:
        if exc.code == "render_already_complete":
            return repository.get_render_job_by_id(render_job_id=job.id)
        raise
    owner = job.attempt_id
    export_storage_path = (
        f"workspaces/{job.workspace_id}/projects/{job.project_id}/exports/{job.id}.mp4"
    )
    file_size_bytes = None
    benchmark_ms = 0
    rendered_broll_count = 0
    storage = create_object_storage(
        root=storage_root,
        supabase_url=supabase_url,
        service_role_key=service_role_key,
    )
    artifact_uploaded = False
    ass_path = None
    if job.manifest.get("include_captions", True):
        ass_path = Path(storage_root).expanduser().resolve() / "tmp" / f"{job.id}.ass"
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        ass_path.write_text(str(job.manifest["caption_ass"]), encoding="utf-8")
    try:
        preflight = _run_render_preflight(
            job=job,
            repository=repository,
            export_bucket=export_bucket,
            export_storage_path=export_storage_path,
            storage_root=storage_root,
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            ass_path=ass_path,
            allow_fixture_media=allow_fixture_media,
        )
        storage = preflight["storage"]
        source_path = preflight["source_path"]
        output_path = preflight["output_path"]
        broll_assets = preflight["broll_assets"]
        rendered_broll_count = len(broll_assets)
        audio_mix = preflight["audio_mix"]
        effects_config = preflight["effects_config"]
        matte_path = preflight["matte_path"]
        job = repository.transition_job(
            job=job,
            status=RenderJobStatus.RENDERING,
            progress_pct=70,
            message=_rendering_message(job),
            event_type="rendering_started",
            attempt_id=owner,
        )
        _ensure_render_not_cancelled(job=job, repository=repository)
        benchmark_ms, file_size_bytes = renderer.render(
            source_path=source_path,
            ass_path=ass_path,
            output_path=output_path,
            specs=job.specs,
            broll_assets=broll_assets,
            effects_config=effects_config,
            manual_elements=_manual_elements_from_manifest(job.manifest),
            framing=_framing_from_manifest(job.manifest),
            keep_segments=job.keep_segments,
            video_encoder=_video_encoder_from_manifest(job.manifest),
            watermark=_watermark_from_manifest(job.manifest),
            matte_path=matte_path,
            cancel_check=lambda: _ensure_render_not_cancelled(
                job=job,
                repository=repository,
            ),
            **audio_mix,
        )
        _ensure_render_not_cancelled(job=job, repository=repository)
        job = repository.transition_job(
            job=job,
            status=RenderJobStatus.UPLOADING,
            progress_pct=90,
            message="Uploading render output.",
            event_type="upload_started",
            attempt_id=owner,
        )
        storage.upload_file(
            bucket=export_bucket,
            path=export_storage_path,
            source_path=output_path,
            content_type="video/mp4",
        )
        artifact_uploaded = True
    except ApiError as exc:
        if exc.code == "render_attempt_superseded":
            # The render belongs to another attempt now: write nothing.
            raise
        job.logs = [*job.logs, _render_failure_log(exc)]
        if exc.code == "render_cancelled":
            return _return_cancelled_render_job(
                job=job,
                repository=repository,
                message=exc.message,
            )
        return repository.transition_job(
            job=job,
            status=RenderJobStatus.FAILED,
            progress_pct=job.progress_pct,
            message=exc.message,
            error_code=exc.code,
            error_message=exc.message,
            event_type=(
                "preflight_failed" if job.status == RenderJobStatus.PREPARING else "failed"
            ),
            attempt_id=owner,
        )
    finally:
        if ass_path is not None:
            ass_path.unlink(missing_ok=True)
    try:
        _ensure_render_not_cancelled(job=job, repository=repository)
    except ApiError as exc:
        if exc.code != "render_cancelled":
            raise
        if artifact_uploaded:
            try:
                storage.delete_object(bucket=export_bucket, path=export_storage_path)
            except Exception as cleanup_error:
                raise ApiError(
                    502,
                    "render_cancellation_cleanup_failed",
                    "Canceled render output could not be deleted.",
                ) from cleanup_error
        return _return_cancelled_render_job(
            job=job,
            repository=repository,
            message=exc.message,
        )
    # Publish only while this attempt still owns the render: a superseded
    # attempt must not add an export row for the newer attempt's render.
    _ensure_attempt_owns_render(job=job, repository=repository, attempt_id=owner)
    export = repository.create_export(
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        render_job_id=job.id,
        storage_bucket=export_bucket,
        storage_path=export_storage_path,
        format=job.specs.format,
        width=job.specs.width,
        height=job.specs.height,
        fps=job.specs.fps,
        duration_ms=sum(segment.end_ms - segment.start_ms for segment in job.keep_segments),
        file_size_bytes=file_size_bytes,
        settings_summary=_settings_summary_from_job(job),
    )
    job.artifact_path = export.storage_path
    job.benchmark_ms = benchmark_ms
    job.logs = [
        *job.logs,
        "local ffmpeg render completed",
        f"broll_render_assets={rendered_broll_count}",
        f"music_mix={_audio_mix_log(job.manifest)}",
        f"effects_config={_effects_log(job.manifest)}",
        f"export_path={export.storage_path}",
    ]
    _record_render_metrics(metrics_registry, benchmark_ms=benchmark_ms)
    _record_export_metrics(
        metrics_registry,
        export_duration_ms=benchmark_ms,
        export_file_size_bytes=file_size_bytes,
    )
    return repository.transition_job(
        job=job,
        status=RenderJobStatus.COMPLETE,
        progress_pct=100,
        message="Export is ready to download.",
        event_type="completed",
        metadata={"export_id": str(export.id)},
        attempt_id=owner,
    )


def _record_render_metrics(metrics_registry: object | None, *, benchmark_ms: int | None) -> None:
    if metrics_registry is None or benchmark_ms is None or not hasattr(metrics_registry, "observe"):
        return
    metrics_registry.observe("render_latency_ms", benchmark_ms, engine="ffmpeg")
    metrics_registry.observe("render_duration_ms", benchmark_ms, engine="ffmpeg")


def _record_export_metrics(
    metrics_registry: object | None,
    *,
    export_duration_ms: int | None,
    export_file_size_bytes: int | None,
) -> None:
    if metrics_registry is None or not hasattr(metrics_registry, "observe"):
        return
    if export_duration_ms is not None:
        metrics_registry.observe("export_latency_ms", export_duration_ms, format="mp4")
        metrics_registry.observe("export_duration_ms", export_duration_ms, format="mp4")
    if export_file_size_bytes is not None:
        metrics_registry.observe("export_file_size_bytes", export_file_size_bytes, format="mp4")


def complete_render_job_by_id(
    *,
    repository: InMemoryRenderRepository,
    metrics_registry: object | None = None,
    workspace_id: UUID,
    project_id: UUID,
    render_job_id: UUID,
    export_bucket: str,
    storage_root: str,
    supabase_url: str | None,
    service_role_key: str | None,
    renderer: object | None = None,
    allow_fixture_media: bool = False,
    attempt_id: UUID | None = None,
) -> RenderJobRecord:
    job = repository.get_render_job(
        project_id=project_id,
        render_job_id=render_job_id,
        workspace_id=workspace_id,
    )
    return complete_render_job(
        job=job,
        repository=repository,
        metrics_registry=metrics_registry,
        export_bucket=export_bucket,
        storage_root=storage_root,
        supabase_url=supabase_url,
        service_role_key=service_role_key,
        renderer=renderer,
        allow_fixture_media=allow_fixture_media,
        attempt_id=attempt_id,
    )


def _return_cancelled_render_job(
    *,
    job: RenderJobRecord,
    repository: InMemoryRenderRepository,
    message: str,
) -> RenderJobRecord:
    latest = repository.get_render_job_by_id(render_job_id=job.id)
    if latest.status == RenderJobStatus.CANCELED:
        return latest
    return repository.transition_job(
        job=job,
        status=RenderJobStatus.CANCELED,
        progress_pct=job.progress_pct,
        message=message,
        event_type="cancelled",
        error_code="render_cancelled",
        error_message=message,
        attempt_id=job.attempt_id,
    )


def _ensure_attempt_owns_render(
    *,
    job: RenderJobRecord,
    repository: InMemoryRenderRepository,
    attempt_id: UUID | None,
) -> None:
    if attempt_id is None:
        return
    latest = repository.get_render_job_by_id(render_job_id=job.id)
    if latest.attempt_id != attempt_id:
        raise ApiError(
            409,
            "render_attempt_superseded",
            "Another attempt owns this render now; this attempt stops.",
        )


def _ensure_render_not_cancelled(
    *,
    job: RenderJobRecord,
    repository: InMemoryRenderRepository,
) -> None:
    latest = repository.get_render_job_by_id(render_job_id=job.id)
    if latest.status == RenderJobStatus.CANCELED:
        raise ApiError(409, "render_cancelled", "Render canceled before completion.")


def _run_render_preflight(
    *,
    job: RenderJobRecord,
    repository: InMemoryRenderRepository,
    export_bucket: str,
    export_storage_path: str,
    storage_root: str,
    supabase_url: str | None,
    service_role_key: str | None,
    ass_path: Path | None,
    allow_fixture_media: bool,
) -> dict[str, object]:
    _ensure_render_not_cancelled(job=job, repository=repository)
    storage = create_object_storage(
        root=storage_root,
        supabase_url=supabase_url,
        service_role_key=service_role_key,
    )
    source = job.manifest.get("source")
    if not isinstance(source, dict):
        raise ApiError(
            409,
            "missing_source_video",
            "Source video is missing from the render manifest.",
        )
    try:
        source_reference_id = source.get("reference_id")
        source_path = materialize_worker_object(
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            reference_id=(UUID(str(source_reference_id)) if source_reference_id else None),
            storage=storage,
            bucket=str(source["bucket"]),
            path=str(source["path"]),
            expected_size_bytes=(
                int(source["verified_size_bytes"])
                if source.get("verified_size_bytes") is not None
                else None
            ),
            expected_sha256=(
                str(source["verified_sha256"]) if source.get("verified_sha256") else None
            ),
        )
    except ApiError as exc:
        if exc.code in {"storage_object_missing", "storage_download_failed"}:
            raise ApiError(
                409,
                "missing_source_video",
                "Source video is unavailable for rendering.",
            ) from exc
        raise
    output_path = storage.object_path(bucket=export_bucket, path=export_storage_path)
    _ensure_temp_dir_writable(storage_root=storage_root, render_job_id=job.id)
    _ensure_export_path_writable(output_path=output_path)
    _ensure_ffmpeg_available()
    _validate_render_props_manifest(job.manifest)
    _validate_music_rights(job.manifest, allow_fixture_media=allow_fixture_media)
    _validate_caption_payload(job.manifest, ass_path=ass_path)
    _validate_media_assets(
        job.manifest,
        storage=storage,
        allow_fixture_media=allow_fixture_media,
        workspace_id=job.workspace_id,
    )
    return {
        "storage": storage,
        "source_path": source_path,
        "output_path": output_path,
        "broll_assets": _broll_assets_from_manifest(
            job.manifest,
            storage=storage,
            allow_fixture_media=allow_fixture_media,
        ),
        "audio_mix": _audio_mix_from_manifest(
            job.manifest,
            storage=storage,
            allow_fixture_media=allow_fixture_media,
        ),
        "effects_config": _effects_config_from_manifest(job.manifest),
        "matte_path": _segmentation_matte_path_from_manifest(job.manifest, storage=storage),
    }


def _ensure_ffmpeg_available() -> None:
    if which("ffmpeg") is None:
        raise ApiError(502, "ffmpeg_missing", "FFmpeg is not available in the render environment.")


def _ensure_temp_dir_writable(*, storage_root: str, render_job_id: UUID) -> None:
    temp_dir = Path(storage_root).expanduser().resolve() / "tmp"
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        probe = temp_dir / f"{render_job_id}.preflight"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        raise ApiError(
            502,
            "temp_dir_unwritable",
            "Render temp directory is not writable.",
        ) from exc


def _ensure_export_path_writable(*, output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output_path.parent, delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
    except OSError as exc:
        raise ApiError(
            502,
            "export_bucket_unwritable",
            "Render output storage is not writable.",
        ) from exc


def _ffmpeg_filter_path(path: Path) -> str:
    escaped = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"'{escaped}'"


def _validate_render_props_manifest(manifest: dict) -> None:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        raise ApiError(409, "invalid_remotion_props", "Render props are missing from the manifest.")
    source_video = render_props.get("sourceVideo")
    if not isinstance(source_video, dict) or not source_video.get("src"):
        raise ApiError(409, "invalid_remotion_props", "Source video props are invalid.")
    export_settings = render_props.get("exportSettings")
    if not isinstance(export_settings, dict) or int(export_settings.get("fps") or 0) <= 0:
        raise ApiError(409, "invalid_remotion_props", "Render props are missing a valid FPS.")


def _validate_caption_payload(manifest: dict, *, ass_path: Path | None) -> None:
    if not manifest.get("include_captions", True):
        return
    caption_ass = manifest.get("caption_ass")
    if (
        not isinstance(caption_ass, str)
        or "[Events]" not in caption_ass
        or "Dialogue:" not in caption_ass
    ):
        raise ApiError(409, "invalid_captions", "Caption payload is invalid for rendering.")
    if ass_path is None:
        raise ApiError(409, "invalid_captions", "Caption payload is missing its ASS script.")
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:d=0.1",
            "-vf",
            f"ass={_ffmpeg_filter_path(ass_path)}",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
        env=media_tool_env(),
    )
    if result.returncode != 0:
        raise ApiError(
            409, "ass_compile_failed", "Caption styling could not be compiled for FFmpeg."
        )


def _selection_is_test_track(selection: dict, track: object) -> bool:
    """A catalog track carrying test rights evidence is a generated 'Test track'.

    It is identified by an explicit is_test_track flag or the 'Test track: '
    title prefix the importer stamps on generated tracks.
    """
    for candidate in (track, selection):
        if isinstance(candidate, dict):
            if candidate.get("is_test_track"):
                return True
            title = str(candidate.get("title") or "")
            if title.startswith("Test track: "):
                return True
            metadata = candidate.get("raw_metadata")
            if isinstance(metadata, dict) and metadata.get("is_test_track"):
                return True
    return False


def _validate_music_rights(manifest: dict, *, allow_fixture_media: bool) -> None:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        return
    selection = render_props.get("musicSelection")
    if not isinstance(selection, dict) or not selection.get("music_track_id"):
        return
    track = selection.get("track")
    if allow_fixture_media and isinstance(track, dict) and track.get("provider") == "fixture":
        return
    # A generated "Test track" (test rights evidence) must never render outside a
    # fixture-enabled stage. In production ALLOW_FIXTURE_PROVIDERS is false, so a
    # catalog track carrying test evidence is refused here (Task 27 leakage gate).
    if not allow_fixture_media and _selection_is_test_track(selection, track):
        raise ApiError(
            409,
            "music_rights_test_track",
            "Generated test tracks cannot be rendered in production.",
        )
    rights = selection.get("rights")
    if (
        not isinstance(rights, dict)
        or not rights.get("verified")
        or rights.get("status") != "verified"
    ):
        raise ApiError(
            409,
            "music_rights_unverified",
            "Render music requires a verified licensed catalog grant.",
        )
    valid_until = rights.get("valid_until")
    try:
        expires = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ApiError(
            409,
            "music_rights_unverified",
            "Render music is missing a verifiable rights expiry.",
        ) from None
    if expires.tzinfo is None or expires <= datetime.now(UTC):
        raise ApiError(
            409,
            "music_rights_expired",
            "The licensed music grant expired before rendering.",
        )


def _validate_media_assets(
    manifest: dict,
    *,
    storage: LocalObjectStorage,
    allow_fixture_media: bool,
    workspace_id: object | None = None,
) -> None:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        return
    try:
        validate_render_media_manifest(
            render_props,
            allow_fixture=allow_fixture_media,
            workspace_id=workspace_id,
            resolve_dns=True,
            require_dns=True,
        )
    except MediaUrlPolicyError as exc:
        raise ApiError(
            409,
            "unsafe_media_url",
            "One or more media references are not allowed for rendering.",
        ) from exc
    for asset in render_props.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("kind") != "video":
            continue
        if (
            _video_source_for_asset(
                asset,
                storage=storage,
                allow_fixture_media=allow_fixture_media,
            )
            is None
        ):
            raise ApiError(
                409,
                "broll_asset_unavailable",
                "One or more B-roll assets are unavailable.",
            )
    music_selection = render_props.get("musicSelection")
    if isinstance(music_selection, dict):
        track = music_selection.get("track")
        if track is not None and not _music_track_has_renderable_source(track):
            raise ApiError(
                409,
                "music_asset_unavailable",
                "Selected music is unavailable for rendering.",
            )


def _music_track_has_renderable_source(track: object) -> bool:
    if not isinstance(track, dict):
        return False
    raw = track.get("raw_metadata")
    if isinstance(raw, dict):
        if raw.get("source") == "fixture":
            return True
        if raw.get("audio_url") or raw.get("download_url") or raw.get("src"):
            return True
        if raw.get("audio_bucket") and raw.get("audio_path"):
            return True
    return bool(track.get("full_track_url") or track.get("preview_url"))


def _rendering_message(job: RenderJobRecord) -> str:
    framing = _framing_from_manifest(job.manifest)
    aspect = framing.get("output_aspect", "9:16")
    fit_mode = framing.get("fit_mode", "fill")
    caption_text = "captioned " if job.manifest.get("include_captions", True) else ""
    return f"Rendering {caption_text}{aspect} MP4 with {fit_mode} framing."


def _render_failure_log(exc: ApiError) -> str:
    parts = [f"render_failed:{exc.code}"]
    if hasattr(exc, "details"):
        returncode = exc.details.get("returncode")
        stderr_tail = exc.details.get("stderr_tail")
        stdout_tail = exc.details.get("stdout_tail")
        if returncode is not None:
            parts.append(f"returncode={returncode}")
        if stderr_tail:
            parts.append(f"stderr={stderr_tail}")
        if stdout_tail:
            parts.append(f"stdout={stdout_tail}")
    return " ".join(parts)


def _settings_summary_from_job(job: RenderJobRecord) -> dict[str, object]:
    manifest = job.manifest
    export_settings = manifest.get("export_settings")
    template = manifest.get("template")
    broll = manifest.get("broll")
    audio = manifest.get("audio")
    return {
        "source_video_id": manifest.get("source_video_id"),
        "caption_track_id": manifest.get("caption_track_id"),
        "include_captions": manifest.get("include_captions", True),
        "include_broll": manifest.get("include_broll", True),
        "edit_plan_id": manifest.get("edit_plan_id"),
        "music_selection_id": manifest.get("music_selection_id"),
        "effects_config_id": manifest.get("effects_config_id"),
        "template_id": manifest.get("template_id"),
        "broll_selection_version": manifest.get("broll_selection_version"),
        "render_engine": manifest.get("render_engine"),
        "renderer_version": manifest.get("renderer_version"),
        "props_hash": manifest.get("props_hash"),
        "export_settings": export_settings if isinstance(export_settings, dict) else {},
        "template": template if isinstance(template, dict) else {},
        "broll": broll if isinstance(broll, dict) else {},
        "audio": audio if isinstance(audio, dict) else {},
    }


def _framing_from_manifest(manifest: dict) -> dict[str, object]:
    export_settings = manifest.get("export_settings")
    if not isinstance(export_settings, dict):
        return {}
    framing = export_settings.get("framing")
    return framing if isinstance(framing, dict) else {}


def _broll_assets_from_manifest(
    manifest: dict,
    *,
    storage: LocalObjectStorage | None = None,
    allow_fixture_media: bool = False,
) -> list[dict[str, object]]:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        return []
    if not manifest.get("include_broll", True):
        return []
    assets = render_props.get("assets")
    if not isinstance(assets, list):
        return []
    renderable: list[dict[str, object]] = []
    for asset in assets[:8]:
        if not isinstance(asset, dict):
            continue
        src = _video_source_for_asset(
            asset,
            storage=storage,
            allow_fixture_media=allow_fixture_media,
        )
        if src is None:
            continue
        if asset.get("kind") != "video":
            continue
        if asset.get("start_ms") is None or asset.get("end_ms") is None:
            continue
        renderable.append({**asset, "src": src})
    return renderable


def _video_source_for_asset(
    asset: dict[str, object],
    *,
    storage: LocalObjectStorage | None,
    allow_fixture_media: bool,
) -> str | Path | None:
    src = str(asset.get("src") or "")
    if not src:
        return None
    if src.startswith("fixture://"):
        if not allow_fixture_media:
            return None
        if storage is None:
            return None
        return _fixture_broll_video(
            storage=storage,
            fixture_id=src,
            duration_ms=max(
                1000,
                int(asset.get("end_ms") or 0) - int(asset.get("start_ms") or 0),
            ),
        )
    if storage is None:
        return None
    if src.startswith("supabase://"):
        return _resolve_internal_storage_reference(src, storage=storage)
    if src.startswith(("http://", "https://")):
        return _materialize_external_media(src, storage=storage, max_bytes=536_870_912)
    return None


def _resolve_internal_storage_reference(
    value: str,
    *,
    storage: LocalObjectStorage,
) -> Path | None:
    try:
        validate_outbound_media_url(value, allow_supabase=True, resolve_dns=False)
        reference = value.removeprefix("supabase://")
        bucket, path = reference.split("/", 1)
        return storage.ensure_object(bucket=bucket, path=path)
    except (ApiError, MediaUrlPolicyError, ValueError):
        return None


def _materialize_external_media(
    value: str,
    *,
    storage: LocalObjectStorage,
    max_bytes: int,
) -> Path | None:
    try:
        validate_outbound_media_url(value, resolve_dns=True, require_dns=True)
    except MediaUrlPolicyError:
        return None
    suffix = Path(urlsplit(value).path).suffix.lower()
    if suffix not in {".aac", ".flac", ".m4a", ".mp3", ".mp4", ".ogg", ".wav", ".webm"}:
        suffix = ".media"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    target = storage.object_path(bucket="tmp", path=f"outbound-media/{digest}{suffix}")
    if target.exists() and target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".partial")
    downloaded = 0
    try:
        with safe_http_stream(value, timeout=180) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length") or 0)
            if content_length > max_bytes:
                return None
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise MediaUrlPolicyError("Media download exceeds the allowed size.")
                    output.write(chunk)
        if downloaded <= 0:
            return None
        os.replace(temporary, target)
        return target
    except (httpx.HTTPError, OSError, MediaUrlPolicyError, ValueError):
        temporary.unlink(missing_ok=True)
        return None


def _effects_config_from_manifest(manifest: dict) -> dict[str, object] | None:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        return None
    effects_config = render_props.get("effectsConfig")
    return effects_config if isinstance(effects_config, dict) else None


def _segmentation_matte_path_from_manifest(
    manifest: dict,
    *,
    storage: LocalObjectStorage,
) -> Path | None:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        return None
    effects_config = render_props.get("effectsConfig")
    if not isinstance(effects_config, dict):
        return None
    background = effects_config.get("background_removal")
    if not isinstance(background, dict) or not background.get("enabled"):
        return None
    matte_status = render_props.get("segmentationMatteStatus")
    if isinstance(matte_status, dict) and matte_status.get("status") != "available":
        return None
    matte = render_props.get("segmentationMatte")
    if not isinstance(matte, dict):
        return None
    if matte.get("status") not in {None, "completed"}:
        return None
    bucket = matte.get("bucket")
    path = matte.get("path")
    if not isinstance(bucket, str) or not isinstance(path, str):
        src = matte.get("src")
        if isinstance(src, str) and src.startswith("supabase://"):
            without_scheme = src.removeprefix("supabase://")
            slash_idx = without_scheme.find("/")
            if slash_idx < 0:
                return None
            bucket = without_scheme[:slash_idx]
            path = without_scheme[slash_idx + 1 :]
        else:
            return None
    return storage.ensure_object(bucket=bucket, path=path)


def _video_encoder_from_manifest(manifest: dict) -> str:
    encoder = str(manifest.get("video_encoder") or "libx264")
    return encoder if encoder in {"libx264", "h264_nvenc", "hevc_nvenc"} else "libx264"


def _watermark_from_manifest(manifest: dict) -> bool:
    render_props = manifest.get("render_props")
    return bool(render_props.get("watermark")) if isinstance(render_props, dict) else False


def _manual_elements_from_manifest(manifest: dict) -> list[dict[str, object]]:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        return []
    edit_plan = render_props.get("editPlan")
    if not isinstance(edit_plan, dict):
        return []
    elements = edit_plan.get("manual_elements")
    if not isinstance(elements, list):
        return []
    return [item for item in elements if isinstance(item, dict)]


def _audio_mix_from_manifest(
    manifest: dict,
    *,
    storage: LocalObjectStorage,
    allow_fixture_media: bool = False,
) -> dict[str, object]:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        return {}
    dialogue_source = _stem_dialogue_source(render_props=render_props, storage=storage)
    manual_sfx, manual_cues = _manual_sfx_from_render_props(render_props, storage=storage)
    selection = render_props.get("musicSelection")
    if not isinstance(selection, dict):
        return _manual_sfx_mix(manual_sfx, manual_cues, dialogue_source=dialogue_source)
    track = selection.get("track")
    if not isinstance(track, dict):
        return _manual_sfx_mix(manual_sfx, manual_cues, dialogue_source=dialogue_source)
    raw_metadata = track.get("raw_metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    music_source = _audio_source_from_metadata(
        metadata=metadata,
        storage=storage,
        fixture_id=str(track.get("provider_track_id") or track.get("title") or "fixture-music"),
        fixture_enabled=allow_fixture_media
        and (str(track.get("provider") or "") == "fixture" or metadata.get("source") == "fixture"),
    )
    sfx_sources = [
        source
        for item in metadata.get("sfx_urls", [])
        if isinstance(item, str)
        for source in [_usable_external_source(item, storage=storage)]
        if source is not None
    ]
    catalog_sfx_count = len(sfx_sources)
    sfx_sources.extend(manual_sfx)
    music_volume = float(selection.get("music_volume") or 0)
    sfx_intensity = max(float(selection.get("sfx_intensity") or 0), 0.8 if manual_sfx else 0)
    if (
        not sfx_sources
        and sfx_intensity > 0
        and (
            allow_fixture_media
            and (
                str(track.get("provider") or "") == "fixture" or metadata.get("source") == "fixture"
            )
        )
    ):
        sfx_sources = [_fixture_sfx_audio(storage=storage)]
    if music_source is None and not sfx_sources:
        return {"dialogue_source": dialogue_source} if dialogue_source else {}
    beat_map = track.get("beat_map")
    cue_ms = (
        [float(item) for item in beat_map[:catalog_sfx_count]] if isinstance(beat_map, list) else []
    )
    while len(cue_ms) < catalog_sfx_count:
        cue_ms.append(0)
    cue_ms.extend(manual_cues)
    return {
        "dialogue_source": dialogue_source,
        "music_source": music_source,
        "music_volume": music_volume if music_source is not None else 0,
        "sfx_sources": sfx_sources,
        "sfx_intensity": sfx_intensity,
        "sfx_cue_ms": cue_ms,
        "duck_music": True,
    }


def _manual_sfx_from_render_props(
    render_props: dict,
    *,
    storage: LocalObjectStorage,
) -> tuple[list[str | Path], list[float]]:
    edit_plan = render_props.get("editPlan")
    if not isinstance(edit_plan, dict):
        return [], []
    elements = edit_plan.get("manual_elements")
    if not isinstance(elements, list):
        return [], []
    sources: list[str | Path] = []
    cues: list[float] = []
    for element in elements[:64]:
        if not isinstance(element, dict) or element.get("type") != "sfx":
            continue
        properties = element.get("properties")
        if not isinstance(properties, dict):
            continue
        value = properties.get("assetUrl") or properties.get("src")
        if not isinstance(value, str):
            continue
        source = _materialize_audio_data_url(value, storage=storage)
        if source is None:
            source = _usable_external_source(value, storage=storage)
        if source is None:
            continue
        sources.append(source)
        cues.append(float(element.get("start_ms") or 0))
    return sources, cues


def _materialize_audio_data_url(value: str, *, storage: LocalObjectStorage) -> Path | None:
    if not value.startswith("data:"):
        return None
    try:
        content = decode_bounded_audio_data_url(value)
    except MediaUrlPolicyError:
        return None
    target = storage.object_path(
        bucket="tmp",
        path=f"manual-sfx-{hashlib.sha256(content).hexdigest()[:16]}.wav",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(content)
    return target


def _manual_sfx_mix(
    sources: list[str | Path],
    cues: list[float],
    *,
    dialogue_source: str | Path | None = None,
) -> dict[str, object]:
    if not sources:
        return {"dialogue_source": dialogue_source} if dialogue_source else {}
    return {
        "dialogue_source": dialogue_source,
        "music_source": None,
        "music_volume": 0,
        "sfx_sources": sources,
        "sfx_intensity": 0.8,
        "sfx_cue_ms": cues,
        "duck_music": True,
    }


def _stem_dialogue_source(*, render_props: dict, storage: LocalObjectStorage) -> str | Path | None:
    stems = render_props.get("sourceStems")
    if not isinstance(stems, dict):
        return None
    active_stem = stems.get("active", "vocals")
    if active_stem not in {"vocals", "no_vocals"}:
        raise ApiError(
            422,
            "render_selected_stem_unavailable",
            "The selected source stem is unavailable for rendering.",
        )
    selected = stems.get(active_stem)
    if not isinstance(selected, dict):
        raise ApiError(
            422,
            "render_selected_stem_unavailable",
            "The selected source stem is unavailable for rendering.",
        )
    bucket = selected.get("bucket")
    path = selected.get("path")
    if not isinstance(bucket, str) or not bucket or not isinstance(path, str) or not path:
        raise ApiError(
            422,
            "render_selected_stem_unavailable",
            "The selected source stem is unavailable for rendering.",
        )
    try:
        return storage.ensure_object(bucket=bucket, path=path)
    except ApiError as exc:
        raise ApiError(
            422,
            "render_selected_stem_unavailable",
            "The selected source stem is unavailable for rendering.",
        ) from exc


def _audio_source_from_metadata(
    *,
    metadata: dict,
    storage: LocalObjectStorage,
    fixture_id: str,
    fixture_enabled: bool,
) -> str | Path | None:
    for key in ("audio_url", "download_url", "src"):
        value = metadata.get(key)
        if isinstance(value, str):
            source = _usable_external_source(value, storage=storage)
            if source is not None:
                return source
    bucket = (
        metadata.get("audio_bucket") or metadata.get("storage_bucket") or metadata.get("bucket")
    )
    path = metadata.get("audio_path") or metadata.get("storage_path") or metadata.get("path")
    if isinstance(bucket, str) and isinstance(path, str):
        try:
            return storage.ensure_object(bucket=bucket, path=path)
        except ApiError:
            return None
    if fixture_enabled:
        return _fixture_music_audio(storage=storage, fixture_id=fixture_id)
    return None


def _usable_external_source(
    value: str,
    *,
    storage: LocalObjectStorage,
) -> Path | None:
    if not value or value.startswith("fixture://"):
        return None
    if value.startswith("supabase://"):
        return _resolve_internal_storage_reference(value, storage=storage)
    if value.startswith(("http://", "https://")):
        return _materialize_external_media(value, storage=storage, max_bytes=67_108_864)
    return None


def _audio_mix_log(manifest: dict) -> str:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        return "none"
    selection = render_props.get("musicSelection")
    if not isinstance(selection, dict):
        return "none"
    track = selection.get("track")
    if not isinstance(track, dict):
        return "muted"
    return str(track.get("title") or "selected")


def _effects_log(manifest: dict) -> str:
    effects_config = _effects_config_from_manifest(manifest)
    if not effects_config:
        return "none"
    preset = str(effects_config.get("preset") or "unknown")
    motion_effects = effects_config.get("motion_effects")
    motion_count = len(motion_effects) if isinstance(motion_effects, list) else 0
    gradient = effects_config.get("gradient_ramp")
    gradient_enabled = isinstance(gradient, dict) and bool(gradient.get("enabled"))
    return f"{preset}:motion={motion_count}:gradient={int(gradient_enabled)}"


def _fixture_broll_video(
    *,
    storage: LocalObjectStorage,
    fixture_id: str,
    duration_ms: int,
) -> Path:
    duration_s = max(1, min(10, duration_ms / 1000))
    digest = hashlib.sha256(f"{fixture_id}:{duration_ms}".encode()).hexdigest()[:16]
    output_path = storage.object_path(
        bucket="generated-assets",
        path=f"fixtures/broll/{digest}.mp4",
    )
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_fixture_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=720x1280:rate=30:duration={duration_s:.3f}",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x14b8a6@0.32:size=720x1280:rate=30:duration={duration_s:.3f}",
            "-filter_complex",
            "[0:v][1:v]blend=all_mode=screen:all_opacity=0.22,"
            "drawbox=x=0:y=0:w=iw:h=ih:color=0x020617@0.18:t=fill",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ],
        output_path=output_path,
    )
    return output_path


def _fixture_music_audio(*, storage: LocalObjectStorage, fixture_id: str) -> Path:
    digest = hashlib.sha256(f"music:{fixture_id}".encode()).hexdigest()[:16]
    output_path = storage.object_path(
        bucket="generated-assets",
        path=f"fixtures/audio/{digest}.m4a",
    )
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frequency = 176 + int(digest[:2], 16) % 80
    _run_fixture_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=44100:duration=8",
            "-af",
            "aformat=channel_layouts=stereo,volume=0.35,afade=t=in:st=0:d=0.2,afade=t=out:st=7.7:d=0.3",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ],
        output_path=output_path,
    )
    return output_path


def _fixture_sfx_audio(*, storage: LocalObjectStorage) -> Path:
    output_path = storage.object_path(
        bucket="generated-assets",
        path="fixtures/audio/sfx-pop.wav",
    )
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_fixture_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=44100:duration=0.18",
            "-af",
            "aformat=channel_layouts=stereo,volume=0.45,afade=t=out:st=0.12:d=0.06",
            str(output_path),
        ],
        output_path=output_path,
    )
    return output_path


def _run_fixture_ffmpeg(command: list[str], *, output_path: Path) -> None:
    if which("ffmpeg") is None:
        raise ApiError(
            502,
            "ffmpeg_unavailable",
            "FFmpeg is not available in the render environment.",
        )
    result = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=60, env=media_tool_env()
    )
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        raise ApiError(
            502,
            "fixture_asset_generation_failed",
            "Fixture media could not be prepared for rendering.",
            details={
                "returncode": result.returncode,
                "stderr_tail": (result.stderr or "")[-800:],
            },
        )


# ---------------------------------------------------------------------------
# Remotion render completion  (called by the worker after it renders + uploads)
# ---------------------------------------------------------------------------


def complete_remotion_render_job(
    *,
    job: RenderJobRecord,
    repository: InMemoryRenderRepository,
    metrics_registry: object | None = None,
    export_bucket: str,
    export_storage_path: str,
    benchmark_ms: int,
    file_size_bytes: int,
    width: int,
    height: int,
    fps: int,
    duration_ms: int | None,
) -> tuple[RenderJobRecord, ExportRecord]:
    """Publish a Remotion artifact through the authoritative completion CAS."""
    if (width, height, fps) != (job.specs.width, job.specs.height, job.specs.fps):
        raise ApiError(
            409,
            "render_output_specs_mismatch",
            "Rendered output does not match the requested width, height, and FPS.",
        )

    output_duration_ms = (
        duration_ms
        if duration_ms is not None
        else sum(seg.end_ms - seg.start_ms for seg in (job.keep_segments or []))
    )
    job.logs = [
        *(job.logs or []),
        f"remotion render completed in {benchmark_ms}ms",
        f"export_path={export_storage_path}",
        f"file_size_bytes={file_size_bytes}",
        f"dimensions={width}x{height}@{fps}fps",
    ]
    completed, exported = repository.complete_render(
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        render_job_id=job.id,
        storage_bucket=export_bucket,
        storage_path=export_storage_path,
        format=job.specs.format,
        width=width,
        height=height,
        fps=fps,
        duration_ms=output_duration_ms,
        file_size_bytes=file_size_bytes,
        settings_summary={
            **_settings_summary_from_job(job),
            "render_engine": "remotion_premium",
            "renderer_version": "remotion-timeline-v2",
        },
        benchmark_ms=benchmark_ms,
    )
    _record_render_metrics(metrics_registry, benchmark_ms=benchmark_ms)
    _record_export_metrics(
        metrics_registry,
        export_duration_ms=benchmark_ms,
        export_file_size_bytes=file_size_bytes,
    )
    return completed, exported
