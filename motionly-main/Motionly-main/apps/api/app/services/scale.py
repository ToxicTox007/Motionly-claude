import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from app.api.errors import ApiError
from app.repositories.scale import ScaleResourceRecord
from app.schemas.broll import StockOrientation, StockProviderKey
from app.schemas.scale import ScaleResourceStatus
from app.services.async_job_adapter import ASYNC_JOB_TEMP_PREFIX
from app.services.inference_operations import (
    InferenceOperation,
    OperationState,
    route_snapshot,
)
from app.services.local_storage import create_object_storage
from app.services.media_probe import probe_duration_seconds, probe_video
from app.services.media_url_policy import MediaUrlPolicyError, safe_http_stream
from app.services.model_gateway import (
    FeatureQuotaExceeded,
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
    may_have_been_sent,
)
from app.services.subprocess_env import ml_tool_env
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
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=1800, check=False, env=ml_tool_env()
        )
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
    model_gateway=None,
    video_generation_route=None,
    stem_gateway_route=None,
    operations_repository=None,
    inference_operations_repository=None,
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
                model_gateway=model_gateway,
                video_generation_route=video_generation_route,
                operations_repository=operations_repository,
                inference_operations_repository=inference_operations_repository,
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
    # Model identity comes from the registry route (plan Task 14: no local model
    # id remains a code default). The job payload carries the model the API
    # resolved from the route; when absent, fall back to the gateway route's
    # model, and only to a neutral capability label if no route is configured.
    route_model = getattr(stem_gateway_route, "model", None) if stem_gateway_route else None
    model = str(record.payload.get("model") or route_model or "stem-separation")
    if model_gateway is not None and stem_gateway_route is not None:
        duration_s = None
        if (
            stem_gateway_route.input_cost_per_million_usd > 0
            or stem_gateway_route.budget_feature is not None
        ):
            # Remote stems are priced and quota-metered per second of audio:
            # reserve from the real duration, and refuse sources whose length
            # cannot be measured.
            duration_s = probe_duration_seconds(source)
            if duration_s is None:
                raise ApiError(
                    422,
                    "stem_source_duration_unknown",
                    "The source length could not be measured for stem separation.",
                )
        try:
            separated = model_gateway.invoke(
                StemSeparationRequest(source_path=source, model=model, duration_s=duration_s),
                context=ModelCallContext(
                    workspace_id=record.workspace_id,
                    project_id=record.project_id,
                    # The stem job's resource id is a stable idempotency key: a
                    # worker retry of this job reserves the SAME async operation,
                    # so an async_job route resumes polling the recorded remote
                    # job instead of submitting (and paying) a second time (plan
                    # Task 17 "one improvement", invariant 6). Local/synchronous
                    # routes ignore it.
                    idempotency_key=f"stem_separation:{record.id}",
                    required_route_id=stem_gateway_route.route_id,
                    required_model_revision=stem_gateway_route.model_revision,
                ),
            )
        except FeatureQuotaExceeded as exc:
            # Stems are user-requested, so a used-up quota fails visibly (402)
            # instead of degrading like the analysis features.
            raise ApiError(402, "audio_stems_quota_exceeded", str(exc)) from exc
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
    result: dict[str, Any] = {
        # Demucs and the fixture separator keep the historical "demucs" label.
        "provider": getattr(stem_separator, "provider_name", "demucs"),
        "model": getattr(stem_separator, "model", None) or model,
        "active": "vocals",
    }
    if provenance is None:
        # Non-production stems are produced by the labelled FixtureStemSeparator
        # (fixtures/registry.json id "stem-separation", label "Test stems"); mark
        # the result so the Audio tab can show that badge next to the stem choice.
        result["stem_provider"] = "fixture"
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
            (
                "motionly-demucs-",
                "motionly-fixture-stems-",
                ASYNC_JOB_TEMP_PREFIX,
            )
        ):
            shutil.rmtree(parent, ignore_errors=True)
            return


def _generate_broll(
    *,
    record,
    settings,
    broll_repository,
    model_gateway=None,
    video_generation_route=None,
    operations_repository=None,
    inference_operations_repository=None,
    billing_repository=None,
    repository=None,
) -> dict[str, Any]:
    """Generate B-roll through a neutral async video operation (plan Task 18).

    The clip is submitted with ``VideoGenerationSubmitRequest`` (capability
    ``video.generation.submit``) and polled with ``VideoGenerationPollRequest``
    (``video.generation.status``), both bound to the stored route id and
    revision so a resumed job polls the original route and never resubmits (plan
    invariant 6, Section 2.5). A durable ``inference_operations`` row records the
    route snapshot, the external operation id and the Section 2.5 state trace.

    Operation ownership (single-owner rule): ``_generate_broll`` owns the
    operation record for a blocking video route. If the resolved route's adapter
    is an ``AsyncJobAdapter`` (an ``async_job`` route that persists its own
    operation), this function does not reserve a second record and lets the
    adapter own it. The Tier 1 ``async_job`` adapter does not serve video
    generation (Section 2.10), so this branch is defensive; a blocking video
    route always resolves here, where ``_generate_broll`` is the single owner.
    """
    if not record.project_id:
        raise ApiError(422, "scale_project_required", "Generated B-roll needs a project.")
    payload = record.payload

    if model_gateway is None or video_generation_route is None:
        # No ``video.generation.submit`` route is configured (plan Task 18).
        raise ApiError(
            503,
            "capability_unavailable",
            "Generated B-roll requires a configured video generation route.",
        )

    from app.services.billing import check_quota

    duration_s = int(payload.get("duration_s") or 5)
    ratio = str(payload.get("ratio") or "768:1280")

    # Pre-call quota check (feature-keyed, Decision B-1 / Task 10). The feature
    # scope sums usage across provider labels; the provider label is now the
    # neutral ``generated`` key rather than a vendor name.
    if billing_repository is not None and operations_repository is not None:
        check_quota(
            workspace_id=record.workspace_id,
            feature="video_generation",
            category="generated_seconds",
            billing_repository=billing_repository,
            operations_repository=operations_repository,
            estimated_quantity=float(duration_s),
            provider="generated",
        )

    adapter_owns_operation = _route_adapter_is_async_job(model_gateway, video_generation_route)

    # Non-blocking async video (plan Task 28, Tier 3 gate item 2). When the
    # served ``video.generation`` route is an ``async_job`` route whose mapping
    # declares ``mode: non_blocking``, generated B-roll runs through the
    # ``InferencePollingEngine``: it submits once (route-affine, never
    # resubmitting after a restart), stores the inference operation id on the
    # phase5 payload with neutral keys, and advances the job with delayed polls
    # instead of holding a worker slot on a blocking poll loop. Blocking routes
    # fall through to the unchanged Tier 1 path below.
    async_adapter = _async_job_adapter_for(model_gateway, video_generation_route)
    if async_adapter is not None and inference_operations_repository is not None:
        route_config = async_adapter.route_config(video_generation_route.route_id)
        if route_config is not None and type(async_adapter).is_non_blocking(route_config):
            video_url = _generate_broll_non_blocking(
                record=record,
                payload=payload,
                duration_s=duration_s,
                ratio=ratio,
                adapter=async_adapter,
                route=video_generation_route,
                route_config=route_config,
                inference_operations_repository=inference_operations_repository,
                repository=repository,
            )
            return _finalize_generated_broll(
                record=record,
                settings=settings,
                broll_repository=broll_repository,
                payload=payload,
                duration_s=duration_s,
                ratio=ratio,
                video_url=video_url,
                operation_id=payload.get("inference_operation_id"),
                operations_repository=operations_repository,
            )

    operation: InferenceOperation | None = None
    if inference_operations_repository is not None and not adapter_owns_operation:
        idempotency_key = f"generated_broll:{record.id}"
        operation, _created = inference_operations_repository.reserve(
            workspace_id=record.workspace_id,
            project_id=record.project_id,
            capability="video.generation.submit",
            idempotency_key=idempotency_key,
            route_id=video_generation_route.route_id,
            model=video_generation_route.model,
            model_revision=video_generation_route.model_revision,
            route_snapshot=route_snapshot(
                adapter_id=str(video_generation_route.adapter),
                adapter_type="async_submit",
                base_url=str(video_generation_route.endpoint_host or ""),
                model=video_generation_route.model,
                model_revision=video_generation_route.model_revision,
                mapping_version="1",
                secret_ref_name=None,
            ),
            estimated_cost_usd=float(payload.get("unit_cost_usd") or 0) * duration_s,
        )

    operation_id = payload.get("inference_operation_id") or (
        operation.provider_operation_id if operation is not None else None
    )
    if operation is not None and operation.provider_operation_id:
        # Resumed after the submit boundary: poll the recorded job instead of
        # resubmitting (plan invariant 6, Section 2.5).
        operation_id = operation.provider_operation_id

    if not operation_id:
        operation = _transition_operation(
            inference_operations_repository,
            operation,
            OperationState.SUBMITTING,
            expected_from={OperationState.RESERVED},
        )
        submit_idempotency_key = (
            operation.idempotency_key if operation is not None else f"generated_broll:{record.id}"
        )
        try:
            submitted = model_gateway.invoke(
                VideoGenerationSubmitRequest(
                    prompt=str(payload["prompt"]),
                    prompt_image_url=(
                        str(payload["prompt_image_url"])
                        if payload.get("prompt_image_url")
                        else None
                    ),
                    duration_s=duration_s,
                    ratio=ratio,
                    idempotency_key=submit_idempotency_key,
                ),
                context=ModelCallContext(
                    workspace_id=record.workspace_id,
                    project_id=record.project_id,
                    idempotency_key=submit_idempotency_key,
                    required_route_id=video_generation_route.route_id,
                    required_model_revision=video_generation_route.model_revision,
                ),
            )
        except Exception as exc:
            # A failure that may have reached the provider is route-affine: the
            # operation becomes ``submit_unknown`` and is reconciled by polling
            # the original route, never retried on another route (plan Section
            # 2.5). A failure proven before send fails the operation outright.
            # The gateway records whether the request was sent on the attempt
            # telemetry; derive it from that, treating "unknown" conservatively
            # as after-send so a possible remote job is never orphaned.
            after_send = _submit_may_have_been_sent(operations_repository, exc)
            _transition_operation(
                inference_operations_repository,
                operation,
                OperationState.SUBMIT_UNKNOWN if after_send else OperationState.FAILED,
                expected_from={OperationState.SUBMITTING},
                error_code=_submit_error_code(exc),
                error_detail=str(exc),
            )
            raise
        if not isinstance(submitted.output, GenerationOperationOutput):
            raise TypeError("video generation submit returned the wrong typed output")
        operation_id = submitted.output.operation_id
        operation = _transition_operation(
            inference_operations_repository,
            operation,
            OperationState.SUBMITTED,
            expected_from={OperationState.SUBMITTING},
            provider_operation_id=operation_id,
        )
        payload["inference_operation_id"] = (
            str(operation.id) if operation is not None else payload.get("inference_operation_id")
        )
        if repository is not None:
            repository.update(record=record, payload=payload)

    operation = _transition_operation(
        inference_operations_repository,
        operation,
        OperationState.RUNNING,
        expected_from={OperationState.SUBMITTED, OperationState.SUBMIT_UNKNOWN},
    )

    # Blocking poll loop (Tier 1, plan Task 18 / 16B). Poll the bound route until
    # it returns artifacts; a ``GenerationOperationOutput`` means still running.
    max_wait_s = float(getattr(video_generation_route, "timeout_s", None) or 900.0)
    poll_interval_s = 2.0
    deadline = time.monotonic() + max_wait_s
    video_url: str | None = None
    while True:
        try:
            polled = model_gateway.invoke(
                VideoGenerationPollRequest(operation_id=str(operation_id)),
                context=ModelCallContext(
                    workspace_id=record.workspace_id,
                    project_id=record.project_id,
                    required_route_id=video_generation_route.route_id,
                    required_model_revision=video_generation_route.model_revision,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - record failure then re-raise
            _transition_operation(
                inference_operations_repository,
                operation,
                OperationState.FAILED,
                expected_from={OperationState.RUNNING},
                error_code="generation_failed",
                error_detail=str(exc),
            )
            raise
        if isinstance(polled.output, GenerationOutput) and polled.output.artifacts:
            video_url = str(polled.output.artifacts[0].url or "")
            break
        if not isinstance(polled.output, (GenerationOutput, GenerationOperationOutput)):
            raise TypeError("video generation poll returned the wrong typed output")
        if time.monotonic() >= deadline:
            _transition_operation(
                inference_operations_repository,
                operation,
                OperationState.FAILED,
                expected_from={OperationState.RUNNING},
                error_code="generation_timeout",
                error_detail="generated B-roll did not complete within the route deadline",
            )
            raise ApiError(
                504,
                "generated_broll_timeout",
                "Generated B-roll did not complete in time.",
            )
        time.sleep(poll_interval_s)

    operation = _transition_operation(
        inference_operations_repository,
        operation,
        OperationState.SUCCEEDED,
        expected_from={OperationState.RUNNING},
        actual_cost_usd=float(payload.get("unit_cost_usd") or 0) * duration_s,
    )
    return _finalize_generated_broll(
        record=record,
        settings=settings,
        broll_repository=broll_repository,
        payload=payload,
        duration_s=duration_s,
        ratio=ratio,
        video_url=video_url,
        operation_id=operation_id,
        operations_repository=operations_repository,
    )


def _finalize_generated_broll(
    *,
    record,
    settings,
    broll_repository,
    payload: dict[str, Any],
    duration_s: int,
    ratio: str,
    video_url: str | None,
    operation_id: object,
    operations_repository=None,
) -> dict[str, Any]:
    """Record cost, store the generated clip and upsert the B-roll asset.

    Shared by the blocking (Tier 1) and non-blocking (Tier 3, Task 28) video
    paths so both write the artifact and return identical neutral result keys.
    The artifact URL is fetched under the SSRF-safe stream policy regardless of
    which path produced it.
    """
    video_url = video_url or ""
    if operations_repository is not None:
        operations_repository.record_cost(
            workspace_id=record.workspace_id,
            project_id=record.project_id,
            provider="generated",
            category="generated_seconds",
            quantity=float(duration_s),
            unit="second",
            unit_cost_usd=float(payload.get("unit_cost_usd") or 0),
            metadata={
                "resource_id": str(record.id),
                "operation_id": str(operation_id),
            },
        )

    storage_metadata: dict[str, Any] = {}
    if video_url.startswith(("http://", "https://")):
        storage = create_object_storage(
            root=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
        )
        target = storage.object_path(bucket="tmp", path=f"generated-broll-{record.id}.mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream_context = safe_http_stream(
                video_url,
                timeout=180,
            )
            with stream_context as response:
                if response.status_code >= 400:
                    raise ApiError(
                        502,
                        "generated_broll_download_failed",
                        "Generated B-roll could not be saved.",
                    )
                with target.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        output.write(chunk)
        except (httpx.HTTPError, MediaUrlPolicyError) as exc:
            raise ApiError(
                502, "generated_broll_download_failed", "Generated B-roll could not be saved."
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
    portrait = ratio != "1280:768"
    asset = broll_repository.upsert_asset(
        workspace_id=record.workspace_id,
        project_id=record.project_id,
        provider=StockProviderKey.GENERATED,
        provider_asset_id=str(operation_id),
        title=str(payload["prompt"])[:160],
        thumbnail_url=None,
        preview_video_url=video_url,
        download_url=video_url,
        license="generated_user_content",
        width=768 if portrait else 1280,
        height=1280 if portrait else 768,
        duration_ms=duration_s * 1000,
        orientation=StockOrientation.PORTRAIT if portrait else StockOrientation.LANDSCAPE,
        score=1.0,
        raw_metadata={
            "prompt": payload["prompt"],
            "status": "ready",
            "inference_operation_id": payload.get("inference_operation_id"),
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
        "operation_id": str(operation_id),
        "video_url": video_url,
        "inference_operation_id": payload.get("inference_operation_id"),
        **storage_metadata,
        "asset_id": str(asset.id),
    }


def _route_adapter_is_async_job(model_gateway: object, route: object) -> bool:
    """Whether ``route``'s registered adapter is an ``AsyncJobAdapter``.

    When it is, the adapter persists its own ``inference_operations`` record
    (plan Task 16B), so ``_generate_broll`` must not reserve a second one
    (single-owner rule). The Tier 1 ``async_job`` adapter does not serve video
    generation, so this returns ``False`` for every Tier 1 video route.
    """
    adapters = getattr(model_gateway, "_adapters", None)
    adapter_name = getattr(route, "adapter", None)
    if not isinstance(adapters, dict) or adapter_name is None:
        return False
    adapter = adapters.get(adapter_name)
    return adapter is not None and type(adapter).__name__ == "AsyncJobAdapter"


def _async_job_adapter_for(model_gateway: object, route: object):
    """Return ``route``'s registered ``AsyncJobAdapter``, or ``None``.

    Used by the non-blocking video path (plan Task 28) to read the route's
    mapping and drive the polling engine. Returns ``None`` for any route whose
    adapter is not an ``async_job`` adapter, so a blocking/fake route is
    unaffected.
    """
    if not _route_adapter_is_async_job(model_gateway, route):
        return None
    adapters = getattr(model_gateway, "_adapters", None)
    return adapters.get(getattr(route, "adapter", None)) if isinstance(adapters, dict) else None


def _generate_broll_non_blocking(
    *,
    record,
    payload: dict[str, Any],
    duration_s: int,
    ratio: str,
    adapter,
    route,
    route_config,
    inference_operations_repository,
    repository=None,
) -> str:
    """Drive a non-blocking ``async_job`` video route through the poll engine.

    Submits exactly once (route-affine; a worker restart resumes polling the
    recorded operation instead of resubmitting, plan invariant 6), stores the
    inference operation id on the phase5 payload with neutral keys, then advances
    the job with delayed polls until the artifact URL is terminal. The delay
    between polls is the engine's ``retry_after_s`` (the route's poll interval);
    the worker queue carries the same value as ``delay_seconds`` when a poll is
    re-enqueued rather than awaited in-process. Returns the terminal video URL.
    """
    from app.services.inference_polling import (
        InferencePollingEngine,
        PollTarget,
    )

    submit_request = VideoGenerationSubmitRequest(
        prompt=str(payload["prompt"]),
        prompt_image_url=(
            str(payload["prompt_image_url"]) if payload.get("prompt_image_url") else None
        ),
        duration_s=duration_s,
        ratio=ratio,
        idempotency_key=f"generated_broll:{record.id}",
    )
    target = PollTarget(
        adapter=adapter,
        route=route,
        config=route_config,
        request=submit_request,
    )
    engine = InferencePollingEngine(
        operations_repository=inference_operations_repository,
        resolver=lambda _operation: target,
    )

    operation, handle = engine.submit(
        target=target,
        workspace_id=record.workspace_id,
        project_id=record.project_id,
        idempotency_key=f"generated_broll:{record.id}",
    )
    # Persist the neutral operation id so a resumed job polls the recorded
    # operation rather than submitting a second time.
    payload["inference_operation_id"] = str(operation.id)
    if repository is not None:
        repository.update(record=record, payload=payload)

    # Advance the job with delayed polls. Each pending poll yields the delay the
    # worker would apply before re-enqueuing the poll job (delay_seconds); here
    # the same engine drives it to the terminal artifact.
    delay_s = handle.first_delay_s
    deadline = time.monotonic() + float(getattr(route, "timeout_s", None) or 900.0)
    while True:
        result = engine.poll_once(operation.id)
        if result.is_terminal:
            output = result.result.output if result.result is not None else None
            if isinstance(output, GenerationOutput) and output.artifacts:
                return str(output.artifacts[0].url or "")
            raise ApiError(
                502,
                "generated_broll_failed",
                "Generated B-roll did not return a usable artifact.",
            )
        if time.monotonic() >= deadline:
            raise ApiError(
                504,
                "generated_broll_timeout",
                "Generated B-roll did not complete in time.",
            )
        time.sleep(max(0.0, result.retry_after_s or delay_s))


def _transition_operation(
    repository,
    operation: InferenceOperation | None,
    to_state: OperationState,
    *,
    expected_from: set[OperationState] | None = None,
    provider_operation_id: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    actual_cost_usd: float | None = None,
) -> InferenceOperation | None:
    """Advance the operation record when one is owned; a no-op otherwise.

    Returns the refreshed operation (or ``None`` when no record is owned, for
    example when the route's adapter owns it or no repository was provided).
    """
    if repository is None or operation is None:
        return operation
    return repository.transition(
        operation.id,
        to_state=to_state,
        expected_from=expected_from,
        provider_operation_id=provider_operation_id,
        error_code=error_code,
        error_detail=error_detail,
        actual_cost_usd=actual_cost_usd,
    )


def _submit_error_code(exc: Exception) -> str:
    """A bounded, neutral error code for a failed video submit."""
    if isinstance(exc, ProviderCallFailure):
        return exc.error_info.code
    return getattr(exc, "code", None) or "generation_submit_failed"


def _submit_may_have_been_sent(operations_repository, exc: Exception) -> bool:
    """Whether a failed video submit may have reached the provider.

    A ``ProviderCallFailure`` carries the ``sent`` classification directly. The
    gateway wraps a route-affine stop in ``ModelProvidersExhausted``; for that
    case the gateway's attempt telemetry records ``sent`` on the shared cost
    repository's ``provider_events``. "unknown" is treated as after-send so a
    possible remote job is never orphaned (plan Section 2.5).
    """
    if isinstance(exc, ProviderCallFailure):
        return may_have_been_sent(exc.error_info)
    events = getattr(operations_repository, "provider_events", None)
    if isinstance(events, list):
        for event in reversed(events):
            if event.get("success"):
                continue
            usage = event.get("usage")
            if isinstance(usage, dict) and "sent" in usage:
                return str(usage.get("sent")) != "false"
            break
    # No signal: be conservative and treat an unproven failure as after-send.
    return True


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
