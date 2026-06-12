import hashlib
import subprocess
from pathlib import Path
from shutil import which
from uuid import UUID

from app.api.errors import ApiError
from app.repositories.renders import ExportRecord, InMemoryRenderRepository, RenderJobRecord
from app.schemas.renders import RenderJobStatus, RenderSpecs
from app.services.ffmpeg_render import FFmpegCaptionRenderer
from app.services.local_storage import LocalObjectStorage, create_object_storage


def complete_render_job(
    *,
    job: RenderJobRecord,
    repository: InMemoryRenderRepository,
    export_bucket: str,
    storage_root: str,
    supabase_url: str | None,
    service_role_key: str | None,
    execute_ffmpeg: bool,
    allow_fixture_media: bool = False,
) -> RenderJobRecord:
    if job.status == RenderJobStatus.COMPLETE:
        return job
    if job.status == RenderJobStatus.CANCELED:
        raise ApiError(409, "render_canceled", "Canceled renders cannot be processed.")
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

    job = repository.transition_job(
        job=job,
        status=RenderJobStatus.PREPARING,
        progress_pct=20,
        message="Prepared render manifest.",
    )
    job = repository.transition_job(
        job=job,
        status=RenderJobStatus.RENDERING,
        progress_pct=70,
        message=_rendering_message(job),
    )
    export_storage_path = (
        f"workspaces/{job.workspace_id}/projects/{job.project_id}/exports/{job.id}.mp4"
    )
    file_size_bytes = None
    benchmark_ms = 0
    rendered_broll_count = 0
    if execute_ffmpeg:
        ass_path = None
        if job.manifest.get("include_captions", True):
            ass_path = Path(storage_root).expanduser().resolve() / "tmp" / f"{job.id}.ass"
            ass_path.parent.mkdir(parents=True, exist_ok=True)
            ass_path.write_text(str(job.manifest["caption_ass"]), encoding="utf-8")
        try:
            storage = create_object_storage(
                root=storage_root,
                supabase_url=supabase_url,
                service_role_key=service_role_key,
            )
            source = job.manifest["source"]
            source_path = storage.ensure_object(
                bucket=str(source["bucket"]),
                path=str(source["path"]),
            )
            output_path = storage.object_path(bucket=export_bucket, path=export_storage_path)
            renderer = FFmpegCaptionRenderer()
            broll_assets = _broll_assets_from_manifest(
                job.manifest,
                storage=storage,
                allow_fixture_media=allow_fixture_media,
            )
            rendered_broll_count = len(broll_assets)
            audio_mix = _audio_mix_from_manifest(
                job.manifest,
                storage=storage,
                allow_fixture_media=allow_fixture_media,
            )
            effects_config = _effects_config_from_manifest(job.manifest)
            try:
                benchmark_ms, file_size_bytes = renderer.render(
                    source_path=source_path,
                    ass_path=ass_path,
                    output_path=output_path,
                    specs=job.specs,
                    broll_assets=broll_assets,
                    effects_config=effects_config,
                    framing=_framing_from_manifest(job.manifest),
                    keep_segments=job.keep_segments,
                    **audio_mix,
                )
            except ApiError as exc:
                if not _can_retry_with_720p(exc=exc, specs=job.specs):
                    raise
                job.logs = [
                    *job.logs,
                    _render_failure_log(exc),
                ]
                output_path.unlink(missing_ok=True)
                fallback_specs = _fallback_specs_for(job.specs)
                job.logs = [
                    *job.logs,
                    f"render_fallback={fallback_specs.width}x{fallback_specs.height}",
                ]
                benchmark_ms, file_size_bytes = renderer.render(
                    source_path=source_path,
                    ass_path=ass_path,
                    output_path=output_path,
                    specs=fallback_specs,
                    broll_assets=broll_assets,
                    effects_config=effects_config,
                    framing=_framing_from_manifest(job.manifest),
                    keep_segments=job.keep_segments,
                    **audio_mix,
                )
                job.specs = fallback_specs
            storage.upload_file(
                bucket=export_bucket,
                path=export_storage_path,
                source_path=output_path,
                content_type="video/mp4",
            )
        except ApiError as exc:
            job.logs = [*job.logs, _render_failure_log(exc)]
            return repository.transition_job(
                job=job,
                status=RenderJobStatus.FAILED,
                progress_pct=job.progress_pct,
                message=exc.message,
                error_code=exc.code,
                error_message=exc.message,
            )
        finally:
            if ass_path is not None:
                ass_path.unlink(missing_ok=True)
    else:
        file_size_bytes = _write_simulated_export(
            export_bucket=export_bucket,
            export_storage_path=export_storage_path,
            storage_root=storage_root,
            supabase_url=supabase_url,
            service_role_key=service_role_key,
        )
    job = repository.transition_job(
        job=job,
        status=RenderJobStatus.UPLOADING,
        progress_pct=90,
        message="Registering export.",
    )
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
        "local ffmpeg render completed" if execute_ffmpeg else "local render completion simulated",
        f"broll_render_assets={rendered_broll_count}",
        f"music_mix={_audio_mix_log(job.manifest)}",
        f"effects_config={_effects_log(job.manifest)}",
        f"export_path={export.storage_path}",
    ]
    return repository.transition_job(
        job=job,
        status=RenderJobStatus.COMPLETE,
        progress_pct=100,
        message="Export is ready to download.",
        metadata={"export_id": str(export.id)},
    )


def complete_render_job_by_id(
    *,
    repository: InMemoryRenderRepository,
    workspace_id: UUID,
    project_id: UUID,
    render_job_id: UUID,
    export_bucket: str,
    storage_root: str,
    supabase_url: str | None,
    service_role_key: str | None,
    execute_ffmpeg: bool,
    allow_fixture_media: bool = False,
) -> RenderJobRecord:
    job = repository.get_render_job(
        project_id=project_id,
        render_job_id=render_job_id,
        workspace_id=workspace_id,
    )
    return complete_render_job(
        job=job,
        repository=repository,
        export_bucket=export_bucket,
        storage_root=storage_root,
        supabase_url=supabase_url,
        service_role_key=service_role_key,
        execute_ffmpeg=execute_ffmpeg,
        allow_fixture_media=allow_fixture_media,
    )


def _can_retry_with_720p(*, exc: ApiError, specs: RenderSpecs) -> bool:
    return exc.code == "ffmpeg_render_failed" and max(specs.width, specs.height) > 1280


def _fallback_specs_for(specs: RenderSpecs) -> RenderSpecs:
    if specs.width <= 0 or specs.height <= 0:
        return RenderSpecs(width=720, height=1280)
    if specs.height >= specs.width:
        height = 1280
        width = _even(round(height * specs.width / specs.height))
    else:
        width = 1280
        height = _even(round(width * specs.height / specs.width))
    return RenderSpecs(width=width, height=height, fps=specs.fps, format=specs.format)


def _even(value: int) -> int:
    return max(2, int(round(value / 2) * 2))


def _rendering_message(job: RenderJobRecord) -> str:
    framing = _framing_from_manifest(job.manifest)
    aspect = framing.get("output_aspect", "9:16")
    fit_mode = framing.get("fit_mode", "fill")
    caption_text = "captioned " if job.manifest.get("include_captions", True) else ""
    return f"Rendering {caption_text}{aspect} MP4 with {fit_mode} framing."


def _write_simulated_export(
    *,
    export_bucket: str,
    export_storage_path: str,
    storage_root: str,
    supabase_url: str | None,
    service_role_key: str | None,
) -> int:
    storage = create_object_storage(
        root=storage_root,
        supabase_url=supabase_url,
        service_role_key=service_role_key,
    )
    output_path = storage.object_path(bucket=export_bucket, path=export_storage_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if which("ffmpeg"):
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=180x320:d=0.5",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-shortest",
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise ApiError(502, "simulated_render_failed", "Simulated export could not be created.")
    else:
        output_path.write_bytes(_SIMULATED_MP4_BYTES)
    storage.upload_file(
        bucket=export_bucket,
        path=export_storage_path,
        source_path=output_path,
        content_type="video/mp4",
    )
    return output_path.stat().st_size


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
    if src.startswith(("http://", "https://")):
        return src
    local = Path(src).expanduser()
    return local if local.exists() and local.is_file() else None


def _effects_config_from_manifest(manifest: dict) -> dict[str, object] | None:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        return None
    effects_config = render_props.get("effectsConfig")
    return effects_config if isinstance(effects_config, dict) else None


def _audio_mix_from_manifest(
    manifest: dict,
    *,
    storage: LocalObjectStorage,
    allow_fixture_media: bool = False,
) -> dict[str, object]:
    render_props = manifest.get("render_props")
    if not isinstance(render_props, dict):
        return {}
    selection = render_props.get("musicSelection")
    if not isinstance(selection, dict):
        return {}
    track = selection.get("track")
    if not isinstance(track, dict):
        return {}
    raw_metadata = track.get("raw_metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    music_source = _audio_source_from_metadata(
        metadata=metadata,
        storage=storage,
        fixture_id=str(track.get("provider_track_id") or track.get("title") or "fixture-music"),
        fixture_enabled=allow_fixture_media
        and (
            str(track.get("provider") or "") == "fixture"
            or metadata.get("source") == "fixture"
        ),
    )
    sfx_sources = [
        source
        for item in metadata.get("sfx_urls", [])
        if isinstance(item, str)
        for source in [_usable_external_source(item)]
        if source is not None
    ]
    music_volume = float(selection.get("music_volume") or 0)
    sfx_intensity = float(selection.get("sfx_intensity") or 0)
    if not sfx_sources and sfx_intensity > 0 and (
        allow_fixture_media
        and (str(track.get("provider") or "") == "fixture" or metadata.get("source") == "fixture")
    ):
        sfx_sources = [_fixture_sfx_audio(storage=storage)]
    if music_source is None and not sfx_sources:
        return {}
    beat_map = track.get("beat_map")
    return {
        "music_source": music_source,
        "music_volume": music_volume if music_source is not None else 0,
        "sfx_sources": sfx_sources,
        "sfx_intensity": sfx_intensity,
        "sfx_cue_ms": beat_map if isinstance(beat_map, list) else [],
        "duck_music": True,
    }


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
            source = _usable_external_source(value)
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


def _usable_external_source(value: str) -> str | None:
    if not value or value.startswith("fixture://"):
        return None
    if value.startswith(("http://", "https://")):
        return value
    local = Path(value).expanduser()
    return str(local) if local.exists() and local.is_file() else None


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
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
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


_SIMULATED_MP4_BYTES = b"motionly simulated mp4 export\n"


# ---------------------------------------------------------------------------
# Remotion render completion  (called by the worker after it renders + uploads)
# ---------------------------------------------------------------------------


def complete_remotion_render_job(
    *,
    job: RenderJobRecord,
    repository: InMemoryRenderRepository,
    export_bucket: str,
    export_storage_path: str,
    benchmark_ms: int,
    file_size_bytes: int,
    width: int,
    height: int,
    fps: int,
    duration_ms: int | None,
) -> tuple[RenderJobRecord, ExportRecord]:
    """
    Finalises a render job whose MP4 has already been rendered and uploaded
    by the Remotion worker service. Unlike complete_render_job() this function
    performs no I/O — it only updates the in-memory / DB state.

    Returns (updated_job, export_record).
    """
    if job.status == RenderJobStatus.CANCELED:
        raise ApiError(409, "render_canceled", "Canceled renders cannot be completed.")

    # If somehow already COMPLETE (idempotent worker retry), bail out early.
    if job.status == RenderJobStatus.COMPLETE:
        exports = repository.list_exports(
            project_id=job.project_id,
            workspace_id=job.workspace_id,
        )
        latest_export = next(
            (e for e in reversed(exports) if e.render_job_id == job.id),
            None,
        )
        if latest_export is not None:
            return job, latest_export

    job = repository.transition_job(
        job=job,
        status=RenderJobStatus.RENDERING,
        progress_pct=70,
        message="Remotion premium render in progress.",
    )
    job = repository.transition_job(
        job=job,
        status=RenderJobStatus.UPLOADING,
        progress_pct=90,
        message="Registering Remotion export.",
    )

    output_duration_ms = duration_ms if duration_ms is not None else sum(
        seg.end_ms - seg.start_ms for seg in (job.keep_segments or [])
    )

    export = repository.create_export(
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        render_job_id=job.id,
        storage_bucket=export_bucket,
        storage_path=export_storage_path,
        format=getattr(job.specs, "format", "mp4"),
        width=width,
        height=height,
        fps=fps,
        duration_ms=output_duration_ms,
        file_size_bytes=file_size_bytes,
        settings_summary={
            **_settings_summary_from_job(job),
            "render_engine": "remotion_premium",
            "renderer_version": "remotion-premium-v1",
        },
    )

    job.artifact_path = export.storage_path
    job.benchmark_ms = benchmark_ms
    job.logs = [
        *(job.logs or []),
        f"remotion render completed in {benchmark_ms}ms",
        f"export_path={export.storage_path}",
        f"file_size_bytes={file_size_bytes}",
        f"dimensions={width}x{height}@{fps}fps",
    ]

    job = repository.transition_job(
        job=job,
        status=RenderJobStatus.COMPLETE,
        progress_pct=100,
        message="Export is ready to download.",
        metadata={"export_id": str(export.id)},
    )
    return job, export
