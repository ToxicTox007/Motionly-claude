import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from app.api.errors import ApiError
from app.repositories.scale import ScaleResourceRecord
from app.schemas.broll import StockOrientation, StockProviderKey
from app.schemas.scale import ScaleResourceStatus
from app.services.local_storage import create_object_storage
from app.services.media_probe import probe_video


class DemucsStemSeparator:
    def separate(self, *, source_path: Path, model: str) -> dict[str, Path]:
        output_root = Path(tempfile.mkdtemp(prefix="motionly-demucs-"))
        command = [
            sys.executable,
            "-m",
            "demucs",
            "--two-stems=vocals",
            "-n",
            model,
            "-o",
            str(output_root),
            str(source_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
        if result.returncode != 0:
            shutil.rmtree(output_root, ignore_errors=True)
            raise ApiError(
                502, "stem_separation_failed", (result.stderr or "Demucs failed.")[-500:]
            )
        stem_dir = output_root / model / source_path.stem
        stems = {name: stem_dir / f"{name}.wav" for name in ("vocals", "no_vocals")}
        if not all(path.exists() for path in stems.values()):
            shutil.rmtree(output_root, ignore_errors=True)
            raise ApiError(502, "stem_separation_failed", "Demucs did not produce both stems.")
        return stems


class FixtureStemSeparator:
    def separate(self, *, source_path: Path, model: str) -> dict[str, Path]:
        del model
        output_root = Path(tempfile.mkdtemp(prefix="motionly-fixture-stems-"))
        stems = {name: output_root / f"{name}.wav" for name in ("vocals", "no_vocals")}
        for path in stems.values():
            path.write_bytes(source_path.read_bytes() or b"fixture-stem")
        return stems


@dataclass(frozen=True)
class RunwayResult:
    task_id: str
    video_url: str


class RunwayGen3Provider:
    def __init__(self, *, api_key: str, model: str = "gen3a_turbo") -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.dev.runwayml.com/v1"

    def generate(
        self,
        *,
        prompt: str,
        prompt_image_url: str | None,
        duration_s: int,
        ratio: str,
    ) -> RunwayResult:
        if not prompt_image_url:
            raise ApiError(
                422,
                "runway_prompt_image_required",
                "Runway Gen-3 requires a prompt image URL for image-to-video generation.",
            )
        response = httpx.post(
            f"{self.base_url}/image_to_video",
            headers={
                "authorization": f"Bearer {self.api_key}",
                "x-runway-version": "2024-11-06",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "promptImage": prompt_image_url,
                "promptText": prompt,
                "duration": duration_s,
                "ratio": ratio,
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise ApiError(502, "runway_generation_failed", "Runway rejected video generation.")
        task_id = str(response.json().get("id") or "")
        if not task_id:
            raise ApiError(502, "runway_generation_failed", "Runway did not return a task id.")
        for _ in range(300):
            task = httpx.get(
                f"{self.base_url}/tasks/{task_id}",
                headers={
                    "authorization": f"Bearer {self.api_key}",
                    "x-runway-version": "2024-11-06",
                },
                timeout=30,
            )
            if task.status_code >= 400:
                raise ApiError(502, "runway_generation_failed", "Runway task polling failed.")
            data = task.json()
            if data.get("status") == "SUCCEEDED":
                outputs = data.get("output") or []
                if outputs:
                    return RunwayResult(task_id=task_id, video_url=str(outputs[0]))
            if data.get("status") in {"FAILED", "CANCELLED"}:
                raise ApiError(
                    502,
                    "runway_generation_failed",
                    str(data.get("failure") or "Runway generation failed."),
                )
            time.sleep(2)
        raise ApiError(504, "runway_generation_timeout", "Runway generation timed out.")


class FixtureRunwayProvider:
    def generate(
        self, *, prompt: str, prompt_image_url: str | None, duration_s: int, ratio: str
    ) -> RunwayResult:
        del prompt_image_url, duration_s, ratio
        slug = "-".join(prompt.lower().split())[:40] or "generated"
        return RunwayResult(task_id=f"fixture-{slug}", video_url=f"fixture://runway/{slug}.mp4")


def execute_scale_resource(
    *,
    repository,
    resource_id: UUID,
    workspace_id: UUID,
    settings,
    upload_repository,
    broll_repository,
    visual_provider,
    analytics_repository,
    stem_separator,
    runway_provider,
) -> ScaleResourceRecord:
    record = repository.get(resource_id=resource_id, workspace_id=workspace_id)
    if record.status in {ScaleResourceStatus.READY, ScaleResourceStatus.COMPLETED}:
        return record
    repository.update(
        record=record,
        status=ScaleResourceStatus.PROCESSING,
        error_message=None,
    )
    try:
        if record.kind == "stem_separation":
            result = _separate_stems(
                record=record,
                settings=settings,
                upload_repository=upload_repository,
                stem_separator=stem_separator,
            )
        elif record.kind == "generated_broll":
            result = _generate_broll(
                record=record,
                settings=settings,
                broll_repository=broll_repository,
                runway_provider=runway_provider,
            )
        elif record.kind == "style_profile":
            result = _train_style(
                record=record,
                settings=settings,
                visual_provider=visual_provider,
            )
        elif record.kind == "model_training":
            result = _train_preference_adapter(
                record=record,
                analytics_repository=analytics_repository,
            )
        else:
            raise ApiError(422, "scale_job_kind_invalid", "This Phase 5 job cannot be executed.")
        return repository.update(
            record=record,
            status=ScaleResourceStatus.READY,
            result=result,
            revision=record.revision + 1,
        )
    except Exception as exc:
        message = exc.message if isinstance(exc, ApiError) else str(exc)[:500]
        repository.update(
            record=record,
            status=ScaleResourceStatus.FAILED,
            error_message=message,
        )
        raise


def _separate_stems(*, record, settings, upload_repository, stem_separator) -> dict[str, Any]:
    if not record.project_id:
        raise ApiError(422, "scale_project_required", "Stem separation needs a project.")
    upload = upload_repository.get_active_upload(
        project_id=record.project_id,
        workspace_id=record.workspace_id,
    )
    storage = create_object_storage(
        root=settings.media_work_dir,
        supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
        service_role_key=settings.supabase_service_role_key,
    )
    source = storage.ensure_object(bucket=upload.storage_bucket, path=upload.storage_path)
    stems = stem_separator.separate(
        source_path=source,
        model=str(record.payload.get("model") or settings.demucs_model),
    )
    result: dict[str, Any] = {"provider": "demucs", "model": record.payload.get("model")}
    try:
        for name, source_path in stems.items():
            path = (
                f"workspaces/{record.workspace_id}/projects/{record.project_id}/"
                f"stems/{record.id}/{name}.wav"
            )
            storage.upload_file(
                bucket=settings.upload_storage_bucket,
                path=path,
                source_path=source_path,
                content_type="audio/wav",
            )
            result[name] = {"bucket": settings.upload_storage_bucket, "path": path}
    finally:
        _remove_stem_temp_directory(stems)
    return result


def _remove_stem_temp_directory(stems: dict[str, Path]) -> None:
    """Remove only the temporary directory created by a configured stem separator."""
    if not stems:
        return
    temp_root = Path(tempfile.gettempdir()).resolve()
    first_stem = next(iter(stems.values())).resolve()
    for parent in first_stem.parents:
        if parent.parent == temp_root and parent.name.startswith(
            ("motionly-demucs-", "motionly-fixture-stems-")
        ):
            shutil.rmtree(parent, ignore_errors=True)
            return


def _generate_broll(*, record, settings, broll_repository, runway_provider) -> dict[str, Any]:
    if not record.project_id:
        raise ApiError(422, "scale_project_required", "Generated B-roll needs a project.")
    payload = record.payload
    generated = runway_provider.generate(
        prompt=str(payload["prompt"]),
        prompt_image_url=str(payload["prompt_image_url"])
        if payload.get("prompt_image_url")
        else None,
        duration_s=int(payload.get("duration_s") or 5),
        ratio=str(payload.get("ratio") or "768:1280"),
    )
    storage_metadata: dict[str, Any] = {}
    if generated.video_url.startswith(("http://", "https://")):
        response = httpx.get(generated.video_url, timeout=180)
        if response.status_code >= 400:
            raise ApiError(
                502, "runway_asset_download_failed", "Generated B-roll could not be saved."
            )
        storage = create_object_storage(
            root=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
        )
        target = storage.object_path(bucket="tmp", path=f"runway-{record.id}.mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        storage_path = (
            f"workspaces/{record.workspace_id}/projects/{record.project_id}/"
            f"generated-broll/{record.id}.mp4"
        )
        storage.upload_file(
            bucket=settings.upload_storage_bucket,
            path=storage_path,
            source_path=target,
            content_type="video/mp4",
        )
        target.unlink(missing_ok=True)
        storage_metadata = {
            "storage_bucket": settings.upload_storage_bucket,
            "storage_path": storage_path,
        }
    portrait = str(payload.get("ratio")) != "1280:768"
    asset = broll_repository.upsert_asset(
        workspace_id=record.workspace_id,
        project_id=record.project_id,
        provider=StockProviderKey.RUNWAY,
        provider_asset_id=generated.task_id,
        title=str(payload["prompt"])[:160],
        thumbnail_url=None,
        preview_video_url=generated.video_url,
        download_url=generated.video_url,
        license="runway_generated_user_content",
        width=768 if portrait else 1280,
        height=1280 if portrait else 768,
        duration_ms=int(payload.get("duration_s") or 5) * 1000,
        orientation=StockOrientation.PORTRAIT if portrait else StockOrientation.LANDSCAPE,
        score=1.0,
        raw_metadata={
            "runway_task_id": generated.task_id,
            "prompt": payload["prompt"],
            "status": "ready",
            **storage_metadata,
        },
    )
    if payload.get("slot_id"):
        slot = broll_repository.get_slot(
            project_id=record.project_id,
            workspace_id=record.workspace_id,
            slot_id=UUID(str(payload["slot_id"])),
        )
        broll_repository.add_slot_candidates(slot=slot, asset_ids=[asset.id])
        broll_repository.update_slot_selection(slot=slot, asset_id=asset.id, disabled=False)
    return {
        "task_id": generated.task_id,
        "video_url": generated.video_url,
        **storage_metadata,
        "asset_id": str(asset.id),
    }


def _train_style(*, record, settings, visual_provider) -> dict[str, Any]:
    references = [
        item for item in record.payload.get("references", []) if item.get("status") == "ready"
    ]
    if len(references) < 5:
        raise ApiError(
            409, "style_references_incomplete", "Upload five reference videos before training."
        )
    storage = create_object_storage(
        root=settings.media_work_dir,
        supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
        service_role_key=settings.supabase_service_role_key,
    )
    manifests = []
    for reference in references[:5]:
        source = storage.ensure_object(bucket=reference["bucket"], path=reference["path"])
        metadata = probe_video(source)
        manifest = visual_provider.analyze(
            source_path=source,
            metadata=metadata,
            sample_rate_fps=min(settings.visual_sample_fps, 1.0),
            scene_threshold=settings.visual_scene_threshold,
        )
        manifests.append(manifest)
    durations = [manifest.duration_ms or 1 for manifest in manifests]
    scene_rates = [
        len(manifest.scenes) / (duration / 60_000)
        for manifest, duration in zip(manifests, durations, strict=True)
    ]
    target_style = str(record.payload.get("target_style") or "creator_influencer")
    return {
        "reference_count": len(manifests),
        "provider": visual_provider.provider_name,
        "learned_settings": {
            "style": target_style,
            "target_duration_s": round(sum(durations) / len(durations) / 1000),
            "cuts_per_minute": round(sum(scene_rates) / len(scene_rates), 2),
            "broll_density": "high" if sum(scene_rates) / len(scene_rates) > 18 else "medium",
            "caption_direction": "match reference pacing and visual density",
            "visual_summaries": [manifest.summary for manifest in manifests],
        },
    }


def _train_preference_adapter(*, record, analytics_repository) -> dict[str, Any]:
    if not record.project_id:
        raise ApiError(422, "scale_project_required", "Model training needs a project.")
    examples = analytics_repository.fine_tuning_examples(
        project_id=record.project_id,
        workspace_id=record.workspace_id,
        limit=1000,
    )
    minimum = int(record.payload.get("minimum_examples") or 3)
    if len(examples) < minimum:
        raise ApiError(
            409,
            "training_examples_insufficient",
            f"At least {minimum} override signals are required.",
        )
    corrections = [example for example in examples if example.signal.value == "correction"]
    preferences: dict[str, Any] = {}
    for example in examples:
        preferences[example.layer.value] = example.ideal_output
    return {
        "adapter_version": f"workspace-{record.workspace_id}-{record.id}",
        "training_example_count": len(examples),
        "correction_count": len(corrections),
        "preferences": preferences,
        "evaluation": {
            "weighted_signal_score": round(
                sum(example.weight for example in examples) / len(examples), 4
            ),
            "status": "accepted",
        },
    }
