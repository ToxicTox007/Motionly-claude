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
from app.services.media_url_policy import MediaUrlPolicyError, safe_http_stream
from app.services.model_gateway import (
    GenerationOperationOutput,
    GenerationOutput,
    ModelCallContext,
    StemSeparationOutput,
    StemSeparationRequest,
    VideoGenerationPollRequest,
    VideoGenerationSubmitRequest,
)
from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderErrorInfo,
    classify_provider_exception,
    classify_provider_status,
)
from app.services.worker_media import materialize_upload_source


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
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gen3a_turbo",
        api_version: str = "2024-11-06",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.api_version = api_version
        self.base_url = "https://api.dev.runwayml.com/v1"

    @staticmethod
    def _failure(
        error_info: ProviderErrorInfo,
        *,
        message: str | None = None,
    ) -> ProviderCallFailure:
        return ProviderCallFailure(
            provider="runway",
            error_info=error_info,
            message=message,
        )

    def initiate_generation(
        self,
        *,
        prompt: str,
        prompt_image_url: str | None,
        duration_s: int,
        ratio: str,
        idempotency_key: str | None = None,
    ) -> str:
        if not prompt_image_url:
            raise self._failure(
                ProviderErrorInfo(
                    "provider_request_rejected",
                    False,
                    fallback_eligible=False,
                    circuit_worthy=False,
                    billable=False,
                ),
                message="Runway video generation requires a prompt image.",
            )
        import logging

        logger = logging.getLogger(__name__)
        try:
            response = httpx.post(
                f"{self.base_url}/image_to_video",
                headers={
                    "authorization": f"Bearer {self.api_key}",
                    "x-runway-version": self.api_version,
                    "content-type": "application/json",
                    **({"idempotency-key": idempotency_key} if idempotency_key else {}),
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
        except Exception as exc:
            from app.core.logging import redact_string

            logger.error(
                "runway_generation_post_failed",
                extra={
                    "model": self.model,
                    "duration_s": duration_s,
                    "error_type": type(exc).__name__,
                    "error_detail": redact_string(str(exc)),
                },
            )
            raise self._failure(classify_provider_exception(exc)) from None

        if response.status_code >= 400:
            raise self._failure(classify_provider_status(response.status_code))
        try:
            task_id = str(response.json().get("id") or "")
        except (TypeError, ValueError):
            task_id = ""
        if not task_id:
            raise self._failure(
                ProviderErrorInfo(
                    "provider_malformed_response",
                    False,
                    fallback_eligible=True,
                    circuit_worthy=True,
                )
            )
        return task_id

    def poll_task(self, task_id: str) -> str:
        import logging

        logger = logging.getLogger(__name__)
        for _ in range(300):
            try:
                task = httpx.get(
                    f"{self.base_url}/tasks/{task_id}",
                    headers={
                        "authorization": f"Bearer {self.api_key}",
                        "x-runway-version": self.api_version,
                    },
                    timeout=30,
                )
            except Exception as exc:
                from app.core.logging import redact_string

                logger.error(
                    "runway_generation_poll_failed",
                    extra={
                        "task_id": task_id,
                        "error_type": type(exc).__name__,
                        "error_detail": redact_string(str(exc)),
                    },
                )
                raise self._failure(classify_provider_exception(exc)) from None

            if task.status_code >= 400:
                raise self._failure(classify_provider_status(task.status_code))
            try:
                data = task.json()
            except (TypeError, ValueError) as exc:
                raise self._failure(
                    ProviderErrorInfo(
                        "provider_malformed_response",
                        False,
                        fallback_eligible=True,
                        circuit_worthy=True,
                    )
                ) from exc
            if data.get("status") == "SUCCEEDED":
                outputs = data.get("output") or []
                if outputs:
                    return str(outputs[0])
                raise self._failure(
                    ProviderErrorInfo(
                        "provider_malformed_response",
                        False,
                        fallback_eligible=True,
                        circuit_worthy=True,
                    )
                )
            if data.get("status") in {"FAILED", "CANCELLED"}:
                raise self._failure(
                    ProviderErrorInfo(
                        "provider_request_rejected",
                        False,
                        fallback_eligible=False,
                        circuit_worthy=False,
                    ),
                    message="Runway generation did not complete successfully.",
                )
            time.sleep(2)
        raise self._failure(ProviderErrorInfo("provider_timeout", True))

    def generate(
        self,
        *,
        prompt: str,
        prompt_image_url: str | None,
        duration_s: int,
        ratio: str,
    ) -> RunwayResult:
        task_id = self.initiate_generation(
            prompt=prompt,
            prompt_image_url=prompt_image_url,
            duration_s=duration_s,
            ratio=ratio,
        )
        video_url = self.poll_task(task_id)
        return RunwayResult(task_id=task_id, video_url=video_url)


class FixtureRunwayProvider:
    def initiate_generation(
        self,
        *,
        prompt: str,
        prompt_image_url: str | None,
        duration_s: int,
        ratio: str,
    ) -> str:
        del prompt_image_url, duration_s, ratio
        slug = "-".join(prompt.lower().split())[:40] or "generated"
        return f"fixture-{slug}"

    def poll_task(self, task_id: str) -> str:
        slug = task_id.replace("fixture-", "")
        return f"fixture://runway/{slug}.mp4"

    def generate(
        self, *, prompt: str, prompt_image_url: str | None, duration_s: int, ratio: str
    ) -> RunwayResult:
        task_id = self.initiate_generation(
            prompt=prompt,
            prompt_image_url=prompt_image_url,
            duration_s=duration_s,
            ratio=ratio,
        )
        video_url = self.poll_task(task_id)
        return RunwayResult(task_id=task_id, video_url=video_url)


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
    model_gateway=None,
    runway_gateway_route=None,
    stem_gateway_route=None,
    operations_repository=None,
    billing_repository=None,
    editorial_learning_repository=None,
    object_reference_service=None,
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
                model_gateway=model_gateway,
                stem_gateway_route=stem_gateway_route,
            )
        elif record.kind == "generated_broll":
            result = _generate_broll(
                record=record,
                settings=settings,
                broll_repository=broll_repository,
                runway_provider=runway_provider,
                model_gateway=model_gateway,
                runway_gateway_route=runway_gateway_route,
                operations_repository=operations_repository,
                billing_repository=billing_repository,
                repository=repository,
            )
        elif record.kind == "style_profile":
            result = _train_style(
                record=record,
                settings=settings,
                visual_provider=visual_provider,
                object_reference_service=object_reference_service,
            )
        elif record.kind == "model_training":
            result = _train_preference_adapter(
                record=record,
                editorial_learning_repository=editorial_learning_repository,
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


def _separate_stems(
    *,
    record,
    settings,
    upload_repository,
    stem_separator,
    model_gateway=None,
    stem_gateway_route=None,
) -> dict[str, Any]:
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
    source = materialize_upload_source(upload=upload, storage=storage)
    model = str(record.payload.get("model") or settings.demucs_model)
    if model_gateway is not None and stem_gateway_route is not None:
        separated = model_gateway.invoke(
            StemSeparationRequest(source_path=source, model=model),
            context=ModelCallContext(
                workspace_id=record.workspace_id,
                project_id=record.project_id,
                required_route_id=stem_gateway_route.route_id,
                required_model_revision=stem_gateway_route.model_revision,
            ),
        )
        if not isinstance(separated.output, StemSeparationOutput):
            raise TypeError("stem separation route returned the wrong typed output")
        stems = dict(separated.output.stems)
        provenance = separated.metadata.as_dict()
    elif getattr(settings, "app_env", "test") == "production":
        raise ApiError(
            503,
            "stem_gateway_route_required",
            "Production stem separation requires an executable gateway route.",
        )
    else:
        stems = stem_separator.separate(source_path=source, model=model)
        provenance = None
    result: dict[str, Any] = {"provider": "demucs", "model": model}
    if provenance is not None:
        result["model_gateway"] = provenance
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


def _generate_broll(
    *,
    record,
    settings,
    broll_repository,
    runway_provider,
    model_gateway=None,
    runway_gateway_route=None,
    operations_repository=None,
    billing_repository=None,
    repository=None,
) -> dict[str, Any]:
    if not record.project_id:
        raise ApiError(422, "scale_project_required", "Generated B-roll needs a project.")
    payload = record.payload

    from app.services.billing import check_quota

    # Pre-call quota check
    if billing_repository is not None and operations_repository is not None:
        check_quota(
            workspace_id=record.workspace_id,
            provider="runway",
            category="generated_seconds",
            billing_repository=billing_repository,
            operations_repository=operations_repository,
            estimated_quantity=float(payload.get("duration_s") or 5),
        )

    task_id = payload.get("runway_task_id")
    route_id = str(payload.get("model_gateway_route_id") or "") or None
    if not task_id:
        if model_gateway is not None and runway_gateway_route is not None:
            idempotency_key = str(payload.get("model_gateway_idempotency_key") or record.id)
            payload.update(
                {
                    "model_gateway_submit_state": "submitting",
                    "model_gateway_idempotency_key": idempotency_key,
                    "model_gateway_route_id": runway_gateway_route.route_id,
                    "model_revision": runway_gateway_route.model_revision,
                }
            )
            if repository is not None:
                repository.update(record=record, payload=payload)
            submitted = model_gateway.invoke(
                VideoGenerationSubmitRequest(
                    prompt=str(payload["prompt"]),
                    prompt_image_url=(
                        str(payload["prompt_image_url"])
                        if payload.get("prompt_image_url")
                        else None
                    ),
                    duration_s=int(payload.get("duration_s") or 5),
                    ratio=str(payload.get("ratio") or "768:1280"),
                    idempotency_key=idempotency_key,
                ),
                context=ModelCallContext(
                    workspace_id=record.workspace_id,
                    project_id=record.project_id,
                    idempotency_key=idempotency_key,
                    required_route_id=runway_gateway_route.route_id,
                    required_model_revision=runway_gateway_route.model_revision,
                ),
            )
            if not isinstance(submitted.output, GenerationOperationOutput):
                raise TypeError("video generation submit returned the wrong typed output")
            task_id = submitted.output.operation_id
            route_id = submitted.metadata.route_id
            payload.update(
                {
                    "runway_task_id": task_id,
                    "model_gateway_route_id": route_id,
                    "model_provider": submitted.metadata.provider,
                    "model": submitted.metadata.model,
                    "model_revision": submitted.metadata.model_revision,
                    "model_gateway_submit_state": "submitted",
                }
            )
        elif getattr(settings, "app_env", "test") == "production":
            raise ApiError(
                503,
                "runway_gateway_route_required",
                "Production Runway submission requires an executable gateway route.",
            )
        else:
            task_id = runway_provider.initiate_generation(
                prompt=str(payload["prompt"]),
                prompt_image_url=(
                    str(payload["prompt_image_url"]) if payload.get("prompt_image_url") else None
                ),
                duration_s=int(payload.get("duration_s") or 5),
                ratio=str(payload.get("ratio") or "768:1280"),
            )
            payload["runway_task_id"] = task_id
        if repository is not None:
            repository.update(record=record, payload=payload)

    if task_id and route_id is None and getattr(settings, "app_env", "test") == "production":
        configured_revision = getattr(runway_gateway_route, "model_revision", None)
        legacy_revision = str(payload.get("model_revision") or "") or None
        if (
            model_gateway is None
            or runway_gateway_route is None
            or (legacy_revision is not None and legacy_revision != configured_revision)
        ):
            raise ApiError(
                503,
                "runway_legacy_route_unavailable",
                "Legacy Runway polling cannot be safely bound to the configured gateway route.",
            )
        route_id = runway_gateway_route.route_id
        payload.update(
            {
                "model_gateway_route_id": route_id,
                "model_provider": runway_gateway_route.provider,
                "model": runway_gateway_route.model,
                "model_revision": configured_revision,
                "model_gateway_submit_state": "legacy_route_bound",
            }
        )
        if repository is not None:
            repository.update(record=record, payload=payload)

    if getattr(settings, "app_env", "test") == "production" and (
        model_gateway is None
        or runway_gateway_route is None
        or route_id != runway_gateway_route.route_id
        or str(payload.get("model_revision") or "") != runway_gateway_route.model_revision
    ):
        raise ApiError(
            503,
            "runway_gateway_route_unavailable",
            "Runway polling requires the configured gateway route and revision.",
        )

    if model_gateway is not None and route_id is not None:
        polled = model_gateway.invoke(
            VideoGenerationPollRequest(operation_id=str(task_id)),
            context=ModelCallContext(
                workspace_id=record.workspace_id,
                project_id=record.project_id,
                required_route_id=route_id,
                required_model_revision=(
                    str(payload.get("model_revision")) if payload.get("model_revision") else None
                ),
            ),
        )
        if not isinstance(polled.output, GenerationOutput) or not polled.output.artifacts:
            raise TypeError("video generation poll returned the wrong typed output")
        video_url = str(polled.output.artifacts[0].url or "")
    else:
        video_url = runway_provider.poll_task(str(task_id))
    generated = RunwayResult(task_id=str(task_id), video_url=video_url)

    if operations_repository is not None:
        operations_repository.record_cost(
            workspace_id=record.workspace_id,
            project_id=record.project_id,
            provider="runway",
            category="generated_seconds",
            quantity=float(payload.get("duration_s") or 5),
            unit="second",
            unit_cost_usd=float(payload.get("unit_cost_usd") or 0),
            metadata={"resource_id": str(record.id), "task_id": generated.task_id},
        )
    storage_metadata: dict[str, Any] = {}
    if generated.video_url.startswith(("http://", "https://")):
        storage = create_object_storage(
            root=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
        )
        target = storage.object_path(bucket="tmp", path=f"runway-{record.id}.mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream_context = safe_http_stream(
                generated.video_url,
                timeout=180,
            )
            with stream_context as response:
                if response.status_code >= 400:
                    raise ApiError(
                        502, "runway_asset_download_failed", "Generated B-roll could not be saved."
                    )
                with target.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        output.write(chunk)
        except (httpx.HTTPError, MediaUrlPolicyError) as exc:
            raise ApiError(
                502, "runway_asset_download_failed", "Generated B-roll could not be saved."
            ) from exc
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
            "model_gateway_route_id": payload.get("model_gateway_route_id"),
            "model_provider": payload.get("model_provider"),
            "model": payload.get("model"),
            "model_revision": payload.get("model_revision"),
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
        "model_gateway_route_id": payload.get("model_gateway_route_id"),
        "model_revision": payload.get("model_revision"),
        **storage_metadata,
        "asset_id": str(asset.id),
    }


def _train_style(
    *, record, settings, visual_provider, object_reference_service=None
) -> dict[str, Any]:
    references = list(record.payload.get("references", []))
    if len(references) != 5:
        raise ApiError(
            409,
            "style_references_incomplete",
            "Style training requires exactly five verified reference videos.",
        )
    if object_reference_service is None:
        raise ApiError(
            503,
            "object_reference_unavailable",
            "Style training requires durable media reference authority.",
        )
    allowed_bucket = settings.upload_storage_bucket
    expected_prefix = (
        f"workspaces/{record.workspace_id}/projects/{record.project_id}/"
        f"style-references/{record.id}/"
    )
    durable_references = []
    for item in references:
        reference_id = item.get("object_reference_id")
        stable_id = item.get("id")
        if (
            item.get("status") != "verified"
            or not reference_id
            or not stable_id
            or item.get("verified_size_bytes") is None
            or not item.get("verified_sha256")
        ):
            raise ApiError(
                409,
                "style_references_incomplete",
                "Every style reference must be active and server verified.",
            )
        reference = object_reference_service.get_active_reference_sync(
            workspace_id=record.workspace_id,
            project_id=record.project_id,
            reference_id=reference_id,
        )
        if (
            reference.owner_type != "style_profile"
            or reference.owner_id != record.id
            or reference.object_type != "style_reference"
            or reference.storage_bucket != allowed_bucket
            or not reference.storage_path.startswith(expected_prefix)
            or not reference.storage_path.split("/")[-1].startswith(f"{stable_id}.")
            or reference.size_bytes != int(item["verified_size_bytes"])
            or reference.sha256 != str(item["verified_sha256"])
        ):
            raise ApiError(
                409,
                "style_reference_invalid",
                "Style reference is stale, inactive, or outside its project owner.",
            )
        durable_references.append(reference)
    storage = create_object_storage(
        root=settings.media_work_dir,
        supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
        service_role_key=settings.supabase_service_role_key,
    )
    manifests = []
    for reference in durable_references:
        source = storage.ensure_object(
            bucket=reference.storage_bucket,
            path=reference.storage_path,
        )
        metadata = probe_video(source)
        contextual_analyze = getattr(visual_provider, "analyze_with_context", None)
        if callable(contextual_analyze):
            manifest = contextual_analyze(
                source_path=source,
                metadata=metadata,
                sample_rate_fps=min(settings.visual_sample_fps, 1.0),
                scene_threshold=settings.visual_scene_threshold,
                workspace_id=record.workspace_id,
                project_id=record.project_id,
            )
        else:
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


def _train_preference_adapter(*, record, editorial_learning_repository) -> dict[str, Any]:
    if not record.project_id:
        raise ApiError(422, "scale_project_required", "Preference learning needs a project.")
    if editorial_learning_repository is None:
        raise ApiError(
            503,
            "editorial_learning_unavailable",
            "Preference learning requires the editorial governance repository.",
        )
    dataset_value = record.payload.get("editorial_dataset_id")
    user_value = record.payload.get("editorial_user_id")
    if not dataset_value or not user_value:
        raise ApiError(
            409,
            "training_dataset_required",
            "Preference learning requires a frozen consented dataset.",
        )
    dataset = editorial_learning_repository.get_dataset(
        dataset_id=UUID(str(dataset_value)),
        workspace_id=record.workspace_id,
        project_id=record.project_id,
        user_id=UUID(str(user_value)),
    )
    examples = editorial_learning_repository.dataset_deltas(dataset=dataset)
    minimum = int(record.payload.get("minimum_examples") or 1)
    if len(examples) < minimum:
        raise ApiError(
            409,
            "training_examples_insufficient",
            f"At least {minimum} consented edit deltas are required.",
        )
    preferences: dict[str, Any] = {}
    for example in reversed(examples):
        after = example.delta.get("after")
        if example.layer not in preferences and isinstance(after, dict) and after:
            preferences[example.layer] = after
    return {
        "preference_summary_version": f"dataset-{dataset.id}",
        "dataset_id": str(dataset.id),
        "dataset_manifest_hash": dataset.manifest_hash,
        "training_example_count": len(examples),
        "preferences": preferences,
        "evaluation": {
            "status": "candidate",
            "reason": (
                "Offline shadow evaluation and explicit promotion are required before serving."
            ),
        },
    }
