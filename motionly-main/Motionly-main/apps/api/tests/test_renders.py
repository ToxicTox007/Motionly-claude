import asyncio
import subprocess
from shutil import which
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.renders import (
    _select_render_engine,
    cancel_render,
    create_bulk_render,
    get_export_download_url,
    renderer_feature_compatibility,
    restore_export_version,
    retime_caption_segments_for_keep_segments,
    retime_manual_elements_for_keep_segments,
    retime_range_for_keep_segments,
    retry_render,
    source_timeline_for_keep_segments,
)
from app.api.renders import (
    create_render as create_render_api,
)
from app.api.renders import (
    list_exports as list_exports_api,
)
from app.api.renders import (
    list_renders as list_renders_api,
)
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.effects import InMemoryEffectsRepository
from app.repositories.music import InMemoryMusicRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.renders import (
    EXPORT_COLUMNS,
    RENDER_JOB_COLUMNS,
    RENDER_JOB_EVENT_COLUMNS,
    InMemoryRenderRepository,
    SupabaseRenderRepository,
)
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.broll import BrollSlotStatus, StockOrientation, StockProviderKey
from app.schemas.captions import CaptionPresetKey, LanguageHint
from app.schemas.effects import (
    AfterEffectsPreset,
    AutoReframeSettings,
    BackgroundRemovalSettings,
    EffectsStatus,
    EffectsTextureSettings,
    GradientRampSettings,
    MotionEffectKey,
    MotionSettings,
    VisualPresetKey,
)
from app.schemas.music import MusicProviderKey, MusicSelectionStatus
from app.schemas.plans import BrollDensity, EditPlanCut, ManualElement, PlanCreateRequest
from app.schemas.renders import (
    CutSegment,
    ExportFraming,
    ExportOptions,
    OutputAspect,
    PlatformPreset,
    RenderBulkCreateRequest,
    RenderCreateRequest,
    RenderEngine,
    RenderJobStatus,
    RenderSpecs,
    RenderTemplateId,
)
from app.schemas.uploads import UploadStatus
from app.services.caption_chunking import chunk_caption_words
from app.services.caption_presets import CAPTION_SCHEMA_VERSION, get_caption_preset
from app.services.ffmpeg_render import FFmpegCaptionRenderer
from app.services.local_storage import LocalObjectStorage
from app.services.queue import QueueRegistry, RenderQueue
from app.services.render_jobs import complete_render_job
from app.services.transcription_provider import FixtureTranscriptionProvider


async def create_render(**kwargs):
    """Exercise render enqueue and worker completion as separate processes would."""
    response = await create_render_api(**kwargs)
    request = kwargs["request"]
    settings = request.app.state.settings
    if getattr(settings, "should_complete_renders_synchronously", False):
        repository = kwargs["repository"]
        record = repository.get_render_job(
            project_id=kwargs["project_id"],
            render_job_id=response.job_id,
            workspace_id=kwargs["actor"].workspace_id,
        )
        completed = complete_render_job(
            job=record,
            repository=repository,
            export_bucket=settings.export_storage_bucket,
            storage_root=settings.media_work_dir,
            supabase_url=(
                str(getattr(settings, "supabase_url", ""))
                if getattr(settings, "supabase_url", None)
                else None
            ),
            service_role_key=getattr(settings, "supabase_service_role_key", None),
            execute_ffmpeg=settings.render_execute_ffmpeg,
            allow_fixture_media=True,
        )
        return response.model_copy(
            update={
                "job": completed.to_response(repository.events_for_job(completed.id)),
                "status": completed.status,
            }
        )
    return response


class FakeStorageSigner:
    def create_signed_download(self, **_kwargs):
        return SimpleNamespace(url="https://storage.example.com/export.mp4?token=test")


def make_render_repository():
    upload_repository = InMemoryUploadRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = upload_repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=1000,
    )
    upload.status = UploadStatus.READY
    upload.metadata.duration_ms = 7000
    upload.metadata.width = 1920
    upload.metadata.height = 1080
    upload.metadata.fps = 30
    upload.metadata.codec = "h264"
    upload.metadata.orientation = "landscape"
    upload.metadata.has_audio = True
    upload_repository.attach_active_video(project_id=project_id, video_id=upload.id)

    caption_repository = InMemoryCaptionRepository(upload_repository)
    transcript = caption_repository.create_transcript(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status="ready",
        language="en",
        language_hint="auto",
        provider="fixture",
        model="whisper-1",
        confidence=0.95,
        duration_ms=7000,
        raw_payload={},
        words=FixtureTranscriptionProvider()
        .transcribe(audio_path=None, language_hint=LanguageHint.AUTO)
        .words,
    )
    preset = get_caption_preset(CaptionPresetKey.BOLD_INFLUENCER)
    caption_repository.create_caption_track(
        workspace_id=workspace_id,
        project_id=project_id,
        transcript_id=transcript.id,
        preset=CaptionPresetKey.BOLD_INFLUENCER,
        schema_version=CAPTION_SCHEMA_VERSION,
        style=preset.style,
        hide_filler_words=False,
        segments=chunk_caption_words(transcript.words),
    )
    return InMemoryRenderRepository(caption_repository), project_id, workspace_id, user_id


def make_supabase_render_repository() -> SupabaseRenderRepository:
    return SupabaseRenderRepository(
        caption_repository=InMemoryCaptionRepository(InMemoryUploadRepository()),
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )


def seed_broll_slot(
    *,
    broll_repository: InMemoryBrollRepository,
    project_id,
    workspace_id,
):
    asset = broll_repository.upsert_asset(
        workspace_id=workspace_id,
        project_id=project_id,
        provider=StockProviderKey.PEXELS,
        provider_asset_id="pexels-vertical-1",
        title="Founder workspace vertical clip",
        thumbnail_url="https://cdn.example.com/thumb.jpg",
        preview_video_url="https://cdn.example.com/preview.mp4",
        download_url="https://cdn.example.com/download.mp4",
        license="pexels_license",
        width=1080,
        height=1920,
        duration_ms=5000,
        orientation=StockOrientation.PORTRAIT,
        score=0.92,
        raw_metadata={"url": "https://pexels.example.com/video"},
    )
    return broll_repository.create_slot_set(
        workspace_id=workspace_id,
        project_id=project_id,
        cache_key="test-broll",
        slots=[
            {
                "workspace_id": workspace_id,
                "project_id": project_id,
                "edit_plan_id": uuid4(),
                "status": BrollSlotStatus.READY,
                "start_ms": 1200,
                "end_ms": 3600,
                "keywords": ["founder workspace"],
                "intent": "Support transcript moment with founder workspace",
                "density": "medium",
                "priority": 0.8,
                "selected_asset_id": asset.id,
                "candidate_asset_ids": [asset.id],
                "crossfade_ms": 260,
                "provider": asset.provider,
                "score": asset.score,
                "error_code": None,
                "error_message": None,
            }
        ],
    )[0]


def seed_fixture_broll_slot(
    *,
    broll_repository: InMemoryBrollRepository,
    project_id,
    workspace_id,
):
    asset = broll_repository.upsert_asset(
        workspace_id=workspace_id,
        project_id=project_id,
        provider=StockProviderKey.FIXTURE,
        provider_asset_id="fixture-workspace-vertical",
        title="Generated workspace B-roll",
        thumbnail_url=None,
        preview_video_url="fixture://broll/workspace/preview",
        download_url="fixture://broll/workspace/download",
        license="fixture_internal_demo",
        width=1080,
        height=1920,
        duration_ms=4000,
        orientation=StockOrientation.PORTRAIT,
        score=0.92,
        raw_metadata={"fixture": True},
    )
    return broll_repository.create_slot_set(
        workspace_id=workspace_id,
        project_id=project_id,
        cache_key="fixture-broll",
        slots=[
            {
                "workspace_id": workspace_id,
                "project_id": project_id,
                "edit_plan_id": uuid4(),
                "status": BrollSlotStatus.READY,
                "start_ms": 500,
                "end_ms": 2500,
                "keywords": ["workspace"],
                "intent": "Support transcript moment with generated workspace media",
                "density": "medium",
                "priority": 0.8,
                "selected_asset_id": asset.id,
                "candidate_asset_ids": [asset.id],
                "crossfade_ms": 260,
                "provider": asset.provider,
                "score": asset.score,
                "error_code": None,
                "error_message": None,
            }
        ],
    )[0]


def make_request(*, complete_locally: bool = True):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    export_storage_bucket="exports",
                    export_signed_url_ttl_seconds=300,
                    should_complete_renders_synchronously=complete_locally,
                    render_execute_ffmpeg=False,
                    media_work_dir=".motionly/test-storage",
                    billing_enabled=True,
                ),
            ),
        ),
    )


def make_request_with_music(
    music_repository: InMemoryMusicRepository,
    *,
    complete_locally: bool = True,
):
    request = make_request(complete_locally=complete_locally)
    request.app.state.music_repository = music_repository
    return request


def make_request_with_effects(
    effects_repository: InMemoryEffectsRepository,
    *,
    complete_locally: bool = True,
):
    request = make_request(complete_locally=complete_locally)
    request.app.state.effects_repository = effects_repository
    return request


def make_real_render_request(tmp_path, *, music_repository=None, effects_repository=None):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    export_storage_bucket="exports",
                    export_signed_url_ttl_seconds=300,
                    should_complete_renders_synchronously=True,
                    render_execute_ffmpeg=True,
                    media_work_dir=str(tmp_path),
                    billing_enabled=False,
                    supabase_url=None,
                    supabase_service_role_key=None,
                ),
            ),
        ),
    )
    if music_repository is not None:
        request.app.state.music_repository = music_repository
    if effects_repository is not None:
        request.app.state.effects_repository = effects_repository
    return request


def test_render_create_completes_export_and_events() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request()

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    assert response.status == "complete"
    assert response.queue_job_id.startswith(f"render:{project_id}:")
    assert response.job.specs.width == 720
    assert response.job.specs.height == 1280
    assert response.job.manifest["billing"]["watermark"] is True
    assert response.job.keep_segments
    assert response.job.manifest["warnings"]
    assert response.job.template_id == "luxury_reel_v1"
    assert response.job.render_engine == "ffmpeg_basic"
    assert response.job.props_hash == response.job.manifest["props_hash"]
    assert response.job.artifact_path.endswith(".mp4")
    assert [event.to_status for event in response.job.events] == [
        "queued",
        "preparing",
        "rendering",
        "uploading",
        "complete",
    ]
    exports = repository.list_exports(project_id=project_id, workspace_id=workspace_id)
    assert len(exports) == 1
    assert exports[0].storage_path.endswith(".mp4")
    assert exports[0].file_size_bytes
    assert exports[0].settings_summary["export_settings"]["resolution"] == "720p"
    assert exports[0].settings_summary["source_video_id"] == str(response.job.uploaded_video_id)
    assert exports[0].settings_summary["caption_track_id"] == str(response.job.caption_track_id)
    assert exports[0].settings_summary["include_captions"] is True
    assert exports[0].settings_summary["include_broll"] is True
    assert exports[0].settings_summary["export_settings"]["watermark"] is True


def test_render_create_applies_creator_template_to_manifest() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(template_id=RenderTemplateId.PRODUCT_PROMO_REEL_V1),
            request=make_request(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    assert response.job.template_id == RenderTemplateId.PRODUCT_PROMO_REEL_V1
    assert response.job.manifest["template_id"] == "product_promo_reel_v1"
    assert response.job.manifest["template"]["name"] == "Product Promo Reel"
    assert response.job.manifest["render_props"]["templateId"] == "product_promo_reel_v1"


def test_render_create_applies_export_options_and_entitlement_cap() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request()

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest.model_validate(
                {
                    "duration_preset": "short",
                    "export_options": {
                        "platform": "tiktok",
                        "resolution": "4k",
                        "format": "mp4",
                    },
                }
            ),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    settings = response.job.manifest["export_settings"]
    assert settings["platform"] == "tiktok"
    assert settings["platform_metadata"]["delivery"] == "for_you_feed"
    assert settings["requested_resolution"] == "4k"
    assert settings["resolution"] == "720p"
    assert settings["duration_preset"] == "short"
    assert settings["watermark"] is True
    assert response.job.specs.width == 720
    assert response.job.specs.height == 1280
    render_props_settings = response.job.manifest["render_props"]["exportSettings"]
    assert render_props_settings["width"] == 720
    assert render_props_settings["height"] == 1280
    assert render_props_settings["platform"] == "tiktok"
    render_duration_ms = sum(
        segment.end_ms - segment.start_ms for segment in response.job.keep_segments
    )
    assert render_duration_ms <= 15_000
    export = repository.list_exports(project_id=project_id, workspace_id=workspace_id)[0]
    assert export.settings_summary["export_settings"]["platform"] == "tiktok"
    assert export.settings_summary["export_settings"]["requested_resolution"] == "4k"
    assert export.settings_summary["export_settings"]["duration_preset"] == "short"


def test_bulk_render_create_queues_multiple_platform_targets() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request(complete_locally=False)
    request.app.state.settings.billing_enabled = False

    response = asyncio.run(
        create_bulk_render(
            project_id=project_id,
            payload=RenderBulkCreateRequest(
                export_targets=[
                    ExportOptions(
                        platform=PlatformPreset.TIKTOK,
                        framing=ExportFraming(output_aspect=OutputAspect.VERTICAL_9_16),
                    ),
                    ExportOptions(
                        platform=PlatformPreset.YOUTUBE_LANDSCAPE,
                        framing=ExportFraming(output_aspect=OutputAspect.LANDSCAPE_16_9),
                    ),
                ],
            ),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    assert response.status == "queued"
    assert len(response.jobs) == 2
    assert len(response.queue_job_ids) == 2
    assert response.jobs[0].manifest["export_settings"]["platform"] == "tiktok"
    assert response.jobs[0].manifest["render_props"]["exportSettings"]["height"] == 1920
    assert response.jobs[1].manifest["export_settings"]["platform"] == "youtube_landscape"
    assert (
        response.jobs[1].manifest["export_settings"]["platform_metadata"]["delivery"]
        == "youtube_standard"
    )
    assert response.jobs[1].manifest["render_props"]["exportSettings"]["width"] == 1920
    assert response.jobs[1].manifest["render_props"]["exportSettings"]["height"] == 1080


def test_render_create_applies_original_aspect_and_custom_framing() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request(complete_locally=False)
    request.app.state.settings.billing_enabled = False

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest.model_validate(
                {
                    "export_options": {
                        "platform": "instagram",
                        "resolution": "1080p",
                        "format": "mp4",
                        "framing": {
                            "output_aspect": "original",
                            "fit_mode": "custom",
                            "crop": {"x": 0.25, "y": 0.7, "zoom": 1.4},
                            "padding_color": "#111111",
                        },
                    },
                }
            ),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    assert response.status == "queued"
    assert response.job.specs.width == 1920
    assert response.job.specs.height == 1080
    framing = response.job.manifest["export_settings"]["framing"]
    assert framing == {
        "output_aspect": "original",
        "fit_mode": "custom",
        "crop": {"x": 0.25, "y": 0.7, "zoom": 1.4},
        "padding_color": "#111111",
    }
    render_props_settings = response.job.manifest["render_props"]["exportSettings"]
    assert render_props_settings["width"] == 1920
    assert render_props_settings["height"] == 1080
    assert render_props_settings["framing"] == framing


def test_render_timeline_retimes_captions_and_broll_after_cuts() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    track = repository.latest_caption_track(project_id=project_id, workspace_id=workspace_id)
    keep_segments = [
        CutSegment(start_ms=0, end_ms=1200, reason="hook"),
        CutSegment(start_ms=3000, end_ms=4500, reason="payoff"),
    ]

    retimed_segments = retime_caption_segments_for_keep_segments(
        segments=track.segments,
        keep_segments=keep_segments,
    )
    retimed_broll = retime_range_for_keep_segments(
        start_ms=3300,
        end_ms=4100,
        keep_segments=keep_segments,
    )

    assert retimed_segments
    assert retimed_segments[0].start_ms == 0
    assert retimed_broll == (1500, 2300)

    request = make_request()
    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    first_caption = response.job.manifest["render_props"]["captions"][0]
    assert first_caption["start_ms"] == 0
    assert "Dialogue: 0,0:00:00." in response.job.manifest["caption_ass"]


def test_source_timeline_maps_cuts_and_manual_elements_to_output_time() -> None:
    cuts = [
        CutSegment(start_ms=500, end_ms=1500, reason="hook"),
        CutSegment(start_ms=3000, end_ms=4500, reason="payoff"),
    ]
    timeline = source_timeline_for_keep_segments(cuts)
    elements = retime_manual_elements_for_keep_segments(
        elements=[
            {
                "id": "title",
                "type": "text",
                "start_ms": 3200,
                "end_ms": 4000,
                "properties": {"content": "Payoff"},
            }
        ],
        keep_segments=cuts,
    )

    assert timeline["duration_ms"] == 2500
    assert timeline["clips"] == [
        {
            "id": "cut-0",
            "source_start_ms": 500,
            "source_end_ms": 1500,
            "output_start_ms": 0,
            "output_end_ms": 1000,
            "reason": "hook",
        },
        {
            "id": "cut-1",
            "source_start_ms": 3000,
            "source_end_ms": 4500,
            "output_start_ms": 1000,
            "output_end_ms": 2500,
            "reason": "payoff",
        },
    ]
    assert elements[0]["start_ms"] == 1200
    assert elements[0]["end_ms"] == 2000


def test_render_creation_uses_active_edit_plan_cuts_as_authority() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(repository.caption_repository)
    from tests.worker_job_test_helpers import seed_active_plan
    seed_active_plan(
        plan_repository=plan_repository,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=PlanCreateRequest(target_duration_s=10, broll_density=BrollDensity.NONE),
    )
    plan_record = plan_repository.active_plan(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    plan_record.plan = plan_record.plan.model_copy(
        update={
            "cuts": [
                EditPlanCut(start_ms=500, end_ms=1400, reason="manual opening"),
                EditPlanCut(start_ms=4200, end_ms=5600, reason="manual payoff"),
            ]
        }
    )

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(edit_plan_id=plan_record.id),
            request=make_request(complete_locally=False),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=plan_repository,
            render_queue=RenderQueue(),
        )
    )

    assert [(cut.start_ms, cut.end_ms) for cut in response.job.keep_segments] == [
        (500, 1400),
        (4200, 5600),
    ]
    assert response.job.manifest["timeline"]["duration_ms"] == 2300
    assert response.job.manifest["render_props"]["timeline"] == response.job.manifest["timeline"]
    assert response.job.manifest["render_props"]["editPlan"]["cuts"] == [
        {"start_ms": 500, "end_ms": 1400, "reason": "manual opening"},
        {"start_ms": 4200, "end_ms": 5600, "reason": "manual payoff"},
    ]


def test_animated_caption_alone_selects_remotion() -> None:
    settings = SimpleNamespace(
        remotion_enabled=True,
        billing_enabled=True,
    )
    selected = _select_render_engine(
        settings=settings,
        billing_plan_key="creator",
        has_active_broll=False,
        has_music=False,
        has_effects=False,
        caption_animation="karaoke",
    )
    assert selected == RenderEngine.REMOTION_PREMIUM


def test_renderer_feature_matrix_never_silently_drops_premium_features() -> None:
    features = {
        "animated_captions:karaoke",
        "manual_transition",
        "manual_music",
        "broll",
    }
    ffmpeg = renderer_feature_compatibility(
        engine=RenderEngine.FFMPEG_BASIC,
        requested_features=features,
    )
    remotion = renderer_feature_compatibility(
        engine=RenderEngine.REMOTION_PREMIUM,
        requested_features=features,
    )

    assert ffmpeg["unsupported"] == [
        "animated_captions:karaoke",
        "manual_music",
        "manual_transition",
    ]
    assert ffmpeg["downgraded"] == ffmpeg["unsupported"]
    assert remotion["unsupported"] == []
    assert remotion["downgraded"] == []


def test_render_adjustments_change_manifest_and_create_versions() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request()

    punchy = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest.model_validate(
                {"adjustment_action": "make_punchier", "duration_preset": "long"}
            ),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )
    cinematic = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest.model_validate({"adjustment_action": "more_cinematic"}),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    punchy_plan = punchy.job.manifest["render_props"]["editPlan"]
    cinematic_plan = cinematic.job.manifest["render_props"]["editPlan"]
    assert punchy.job.manifest["adjustment_action"] == "make_punchier"
    assert punchy.job.manifest["export_settings"]["resolution"] == "720p"
    assert punchy.job.manifest["export_settings"]["requested_duration_preset"] == "long"
    assert punchy.job.manifest["export_settings"]["duration_preset"] == "short"
    assert "zoom_punch" in punchy_plan["effects"]
    assert cinematic.job.manifest["adjustment_action"] == "more_cinematic"
    assert cinematic_plan["color_grade"] == "cinematic_hotel"
    assert "ken_burns" in cinematic_plan["effects"]
    exports = repository.list_exports(project_id=project_id, workspace_id=workspace_id)
    assert [export.version for export in exports] == [2, 1]


def test_retry_render_requeues_failed_render_only() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request(complete_locally=False)
    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )
    queued = repository.get_render_job(
        project_id=project_id,
        render_job_id=response.job_id,
        workspace_id=workspace_id,
    )
    repository.transition_job(
        job=queued,
        status=RenderJobStatus.FAILED,
        progress_pct=70,
        message="Injected failure.",
        error_code="test_failed",
        error_message="Injected failure.",
    )

    retried = asyncio.run(
        retry_render(
            project_id=project_id,
            render_job_id=response.job_id,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            render_queue=RenderQueue(),
        )
    )

    assert retried.status == RenderJobStatus.QUEUED
    assert retried.job.error_code is None
    assert [event.event_type for event in retried.job.events][-1] == "retry"

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            retry_render(
                project_id=project_id,
                render_job_id=response.job_id,
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                repository=repository,
                render_queue=RenderQueue(),
            )
        )
    assert exc_info.value.code == "render_not_retryable"


def test_render_create_can_hide_caption_burn_in() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request()
    track = repository.latest_caption_track(project_id=project_id, workspace_id=workspace_id)

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest.model_validate(
                {
                    "caption_track_id": str(track.id),
                    "include_captions": False,
                }
            ),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    assert response.status == "complete"
    assert response.job.caption_track_id == track.id
    assert response.job.manifest["include_captions"] is False
    assert response.job.manifest["caption_ass"] is None
    assert response.job.manifest["render_props"]["captions"] == []
    assert response.job.manifest["render_props"]["words"]
    assert "include_captions=False" in response.job.logs


@pytest.mark.parametrize("track_state", ["missing", "empty"])
def test_render_without_captions_does_not_require_caption_track(track_state: str) -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    track = repository.latest_caption_track(project_id=project_id, workspace_id=workspace_id)
    caption_track_id = track.id
    if track_state == "missing":
        repository.caption_repository.caption_tracks.clear()
        caption_track_id = None
    else:
        track.segments = []

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(
                caption_track_id=caption_track_id,
                include_captions=False,
            ),
            request=make_request(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    assert response.status == RenderJobStatus.COMPLETE
    assert response.job.caption_track_id == caption_track_id
    assert response.job.manifest["caption_track_id"] == (
        str(caption_track_id) if caption_track_id else None
    )
    assert response.job.manifest["include_captions"] is False
    assert response.job.manifest["caption_ass"] is None
    assert response.job.manifest["render_props"]["captions"] == []
    assert response.job.manifest["render_props"]["captionStyle"]["font_family"]
    assert response.job.manifest["render_props"]["words"]
    assert response.job.manifest["timeline"]["captions"] == []


def test_render_completion_falls_back_to_720p_after_ffmpeg_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request(complete_locally=False)
    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )
    source_path = tmp_path / "raw-uploads" / "video.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"source")
    calls = []

    def fake_render(
        self, *, source_path, ass_path, output_path, specs, broll_assets=None, **_kwargs
    ):
        calls.append((specs.width, specs.height))
        if len(calls) == 1:
            raise ApiError(
                502,
                "ffmpeg_render_failed",
                "FFmpeg could not render the export.",
                details={"returncode": -9},
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")
        return 123, 3

    monkeypatch.setattr(FFmpegCaptionRenderer, "render", fake_render)

    queued_job = repository.get_render_job(
        project_id=project_id,
        render_job_id=response.job_id,
        workspace_id=workspace_id,
    )
    queued_job.specs = RenderSpecs()

    job = complete_render_job(
        job=queued_job,
        repository=repository,
        export_bucket="exports",
        storage_root=str(tmp_path),
        supabase_url=None,
        service_role_key=None,
        execute_ffmpeg=True,
    )

    assert job.status == RenderJobStatus.COMPLETE
    assert calls == [(1080, 1920), (720, 1280)]
    assert job.specs.width == 720
    assert "render_fallback=720x1280" in job.logs
    assert repository.list_exports(project_id=project_id, workspace_id=workspace_id)[0].width == 720


def test_render_completion_passes_effects_config_to_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(repository.caption_repository)
    effects_repository = InMemoryEffectsRepository(plan_repository)
    effects_repository.create_config(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=None,
        status=EffectsStatus.READY,
        preset=VisualPresetKey.CHAMPAGNE_GOLD,
        lut_intensity=0.8,
        texture=EffectsTextureSettings(grain=0.2, vignette=0.1, light_leak=0.08),
        motion_effects=[MotionEffectKey.FLASH],
        background_removal=BackgroundRemovalSettings(enabled=False),
        gradient_ramp=GradientRampSettings(enabled=True),
        after_effects_presets=[],
        after_effects_project_url=None,
        correction_manifest_url=None,
        effect_count_limit_per_30s=3,
        render_time_impact="medium",
        preview_before_url=None,
        preview_after_url=None,
        cache_key="effects-test",
    )
    request = make_request_with_effects(effects_repository, complete_locally=False)
    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=plan_repository,
            render_queue=RenderQueue(),
        )
    )
    source_path = tmp_path / "raw-uploads" / "video.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"source")
    seen_effects: list[dict | None] = []

    def fake_render(self, *, output_path, effects_config=None, **_kwargs):
        seen_effects.append(effects_config)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")
        return 42, 3

    monkeypatch.setattr(FFmpegCaptionRenderer, "render", fake_render)
    queued_job = repository.get_render_job(
        project_id=project_id,
        render_job_id=response.job_id,
        workspace_id=workspace_id,
    )

    job = complete_render_job(
        job=queued_job,
        repository=repository,
        export_bucket="exports",
        storage_root=str(tmp_path),
        supabase_url=None,
        service_role_key=None,
        execute_ffmpeg=True,
    )

    assert seen_effects[0]["preset"] == "champagne_gold"
    assert seen_effects[0]["gradient_ramp"]["enabled"] is True
    assert "effects_config=champagne_gold:motion=1:gradient=1" in job.logs


def test_render_preflight_fails_when_source_video_is_missing(tmp_path) -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request(complete_locally=False)
    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )
    queued_job = repository.get_render_job(
        project_id=project_id,
        render_job_id=response.job_id,
        workspace_id=workspace_id,
    )

    job = complete_render_job(
        job=queued_job,
        repository=repository,
        export_bucket="exports",
        storage_root=str(tmp_path),
        supabase_url=None,
        service_role_key=None,
        execute_ffmpeg=True,
    )

    assert job.status == RenderJobStatus.FAILED
    assert job.error_code == "missing_source_video"
    assert (
        [event.event_type for event in repository.events_for_job(job.id)][-1]
        == "preflight_failed"
    )


def test_render_preflight_catches_invalid_ass_before_render(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request(complete_locally=False)
    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )
    upload = repository.get_active_upload(project_id=project_id, workspace_id=workspace_id)
    LocalObjectStorage(root=tmp_path).write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"source",
    )
    queued_job = repository.get_render_job(
        project_id=project_id,
        render_job_id=response.job_id,
        workspace_id=workspace_id,
    )
    queued_job.manifest["caption_ass"] = "bad ass"

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stderr="bad ass", stdout="")

    monkeypatch.setattr("app.services.render_jobs.subprocess.run", fake_run)

    job = complete_render_job(
        job=queued_job,
        repository=repository,
        export_bucket="exports",
        storage_root=str(tmp_path),
        supabase_url=None,
        service_role_key=None,
        execute_ffmpeg=True,
    )

    assert job.status == RenderJobStatus.FAILED
    assert job.error_code == "invalid_captions"


def test_cancel_render_marks_job_canceled_and_removes_queue_entry() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    registry = QueueRegistry()
    render_queue = RenderQueue(registry)
    request = make_request(complete_locally=False)
    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=render_queue,
        )
    )

    canceled = asyncio.run(
        cancel_render(
            project_id=project_id,
            render_job_id=response.job_id,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            render_queue=render_queue,
        )
    )

    assert canceled.status == RenderJobStatus.CANCELED
    queue_job_id = RenderQueue.job_id_for_render(
        render_job_id=response.job_id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    assert (
        queue_job_id not in registry.jobs
        or registry.jobs[queue_job_id].status.value == "canceled"
    )


def test_create_export_is_idempotent_for_same_render_job() -> None:
    repository, project_id, workspace_id, _user_id = make_render_repository()
    render_job_id = uuid4()

    first = repository.create_export(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=render_job_id,
        storage_bucket="exports",
        storage_path="exports/render.mp4",
        format="mp4",
        width=720,
        height=1280,
        fps=30,
        duration_ms=1000,
        file_size_bytes=10,
        settings_summary={},
    )
    second = repository.create_export(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=render_job_id,
        storage_bucket="exports",
        storage_path="exports/render.mp4",
        format="mp4",
        width=720,
        height=1280,
        fps=30,
        duration_ms=1000,
        file_size_bytes=10,
        settings_summary={},
    )

    assert first.id == second.id
    assert len(repository.list_exports(project_id=project_id, workspace_id=workspace_id)) == 1


def test_render_and_export_lists_apply_limit() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    first_job = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=uuid4(),
        caption_track_id=uuid4(),
        status=RenderJobStatus.QUEUED,
        planner_version="test",
        manifest={},
        keep_segments=[],
    )
    second_job = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=uuid4(),
        caption_track_id=uuid4(),
        status=RenderJobStatus.RENDERING,
        planner_version="test",
        manifest={},
        keep_segments=[],
    )
    first_export = repository.create_export(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=first_job.id,
        storage_bucket="exports",
        storage_path="exports/first.mp4",
        format="mp4",
        width=720,
        height=1280,
        fps=30,
        settings_summary={},
    )
    second_export = repository.create_export(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=second_job.id,
        storage_bucket="exports",
        storage_path="exports/second.mp4",
        format="mp4",
        width=720,
        height=1280,
        fps=30,
        settings_summary={},
    )

    renders = asyncio.run(
        list_renders_api(
            project_id=project_id,
            actor=actor,
            repository=repository,
            limit=1,
        )
    )
    exports = asyncio.run(
        list_exports_api(
            project_id=project_id,
            actor=actor,
            repository=repository,
            limit=1,
        )
    )

    assert len(renders.jobs) == 1
    assert len(exports.exports) == 1
    assert {renders.jobs[0].id}.issubset({first_job.id, second_job.id})
    assert exports.exports[0].id == second_export.id
    assert first_export.id != second_export.id


@pytest.mark.anyio
async def test_supabase_render_async_lists_use_bounded_projections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = make_supabase_render_repository()
    workspace_id = uuid4()
    project_id = uuid4()
    render_job_id = uuid4()
    export_id = uuid4()
    calls = []
    repository.caption_repository.upload_repository.seed_project(
        project_id=project_id,
        workspace_id=workspace_id,
    )

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/render_jobs":
            return _supabase_response([_render_job_row(render_job_id, project_id, workspace_id)])
        if path == "/render_job_events":
            return _supabase_response([_render_event_row(render_job_id, project_id, workspace_id)])
        if path == "/exports":
            return _supabase_response(
                [_export_row(export_id, render_job_id, project_id, workspace_id)]
            )
        raise AssertionError(f"unexpected Supabase call: {method} {path}")

    monkeypatch.setattr(
        "app.repositories.renders.SupabaseRestClient.async_request",
        fake_async_request,
    )

    jobs = await repository.list_render_jobs_async(
        project_id=project_id,
        workspace_id=workspace_id,
        limit=25,
    )
    events = await repository.events_for_job_async(render_job_id, limit=10)
    exports = await repository.list_exports_async(
        project_id=project_id,
        workspace_id=workspace_id,
        limit=5,
    )
    export = await repository.get_export_async(export_id=export_id, workspace_id=workspace_id)

    assert jobs[0].id == render_job_id
    assert events[0].render_job_id == render_job_id
    assert exports[0].id == export_id
    assert export.id == export_id
    assert calls[0][2]["params"] == {
        "workspace_id": f"eq.{workspace_id}",
        "project_id": f"eq.{project_id}",
        "select": RENDER_JOB_COLUMNS,
        "order": "created_at.desc",
        "limit": "25",
    }
    assert calls[1][2]["params"] == {
        "render_job_id": f"eq.{render_job_id}",
        "select": RENDER_JOB_EVENT_COLUMNS,
        "order": "created_at.asc",
        "limit": "10",
    }
    assert calls[2][2]["params"] == {
        "workspace_id": f"eq.{workspace_id}",
        "project_id": f"eq.{project_id}",
        "select": EXPORT_COLUMNS,
        "order": "version.desc",
        "limit": "5",
    }
    assert calls[3][2]["params"] == {
        "id": f"eq.{export_id}",
        "workspace_id": f"eq.{workspace_id}",
        "select": EXPORT_COLUMNS,
        "limit": "1",
    }


def test_render_api_prefers_async_repository_methods() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    class AsyncRenderRepository(InMemoryRenderRepository):
        def __init__(self, caption_repository):
            super().__init__(caption_repository)
            self.async_calls: list[str] = []

        async def list_render_jobs_async(self, **kwargs):
            self.async_calls.append("list-renders")
            return self.list_render_jobs(**kwargs)

        async def get_render_job_async(self, **kwargs):
            self.async_calls.append("get-render")
            return self.get_render_job(**kwargs)

        async def events_for_job_async(self, render_job_id, *, limit=100):
            self.async_calls.append("events")
            return self.events_for_job(render_job_id, limit=limit)

        async def transition_job_async(self, **kwargs):
            self.async_calls.append("transition")
            return self.transition_job(**kwargs)

        async def list_exports_async(self, **kwargs):
            self.async_calls.append("list-exports")
            return self.list_exports(**kwargs)

        async def get_export_async(self, **kwargs):
            self.async_calls.append("get-export")
            return self.get_export(**kwargs)

    async_repository = AsyncRenderRepository(repository.caption_repository)
    job = async_repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=uuid4(),
        caption_track_id=uuid4(),
        status=RenderJobStatus.QUEUED,
        planner_version="test",
        manifest={},
        keep_segments=[],
    )
    export = async_repository.create_export(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=job.id,
        storage_bucket="exports",
        storage_path="exports/render.mp4",
        format="mp4",
        width=720,
        height=1280,
        fps=30,
        settings_summary={},
    )

    asyncio.run(
        list_renders_api(
            project_id=project_id,
            actor=actor,
            repository=async_repository,
            limit=1,
        )
    )
    asyncio.run(
        cancel_render(
            project_id=project_id,
            render_job_id=job.id,
            actor=actor,
            repository=async_repository,
            render_queue=SimpleNamespace(cancel=lambda *_args, **_kwargs: None),
        )
    )
    asyncio.run(
        list_exports_api(
            project_id=project_id,
            actor=actor,
            repository=async_repository,
            limit=1,
        )
    )
    asyncio.run(
        get_export_download_url(
            export_id=export.id,
            request=SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        settings=SimpleNamespace(export_signed_url_ttl_seconds=60),
                        operations_repository=None,
                    )
                )
            ),
            actor=actor,
            repository=async_repository,
            storage_signer=SimpleNamespace(
                create_signed_download=lambda **_kwargs: SimpleNamespace(url="https://signed")
            ),
        )
    )

    assert async_repository.async_calls == [
        "list-renders",
        "events",
        "get-render",
        "transition",
        "events",
        "list-exports",
        "get-export",
    ]


def _supabase_response(rows):
    return SimpleNamespace(status_code=200, json=lambda: rows)


def _render_job_row(render_job_id, project_id, workspace_id):
    now = "2026-07-06T00:00:00+00:00"
    return {
        "id": str(render_job_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "uploaded_video_id": str(uuid4()),
        "caption_track_id": str(uuid4()),
        "status": "queued",
        "planner_version": "test",
        "manifest": {},
        "keep_segments": [],
        "specs": {"width": 720, "height": 1280, "fps": 30, "format": "mp4"},
        "template_id": None,
        "render_engine": "ffmpeg_basic",
        "renderer_version": "test-renderer",
        "props_hash": "props",
        "artifact_path": None,
        "benchmark_ms": None,
        "logs": [],
        "progress_pct": 5,
        "error_code": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }


def _render_event_row(render_job_id, project_id, workspace_id):
    return {
        "id": str(uuid4()),
        "render_job_id": str(render_job_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "event_type": "queued",
        "from_status": None,
        "to_status": "queued",
        "progress_pct": 5,
        "message": "queued",
        "error_code": None,
        "latency_ms": None,
        "metadata": {},
        "created_at": "2026-07-06T00:00:00+00:00",
    }


def _export_row(export_id, render_job_id, project_id, workspace_id):
    return {
        "id": str(export_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "render_job_id": str(render_job_id),
        "version": 1,
        "storage_bucket": "exports",
        "storage_path": "exports/render.mp4",
        "format": "mp4",
        "width": 720,
        "height": 1280,
        "fps": 30,
        "duration_ms": 1_000,
        "file_size_bytes": 10,
        "thumbnail_url": None,
        "settings_summary": {},
        "restored_from_export_id": None,
        "manifest": {},
        "created_at": "2026-07-06T00:00:00+00:00",
    }


def test_supabase_render_job_hydrates_from_live_schema_compat_logs() -> None:
    repository = make_supabase_render_repository()
    workspace_id = uuid4()
    project_id = uuid4()
    upload_id = uuid4()
    track_id = uuid4()
    job_id = uuid4()
    now = "2026-06-03T07:00:00+00:00"

    job = repository._job_from_row(
        {
            "id": str(job_id),
            "workspace_id": str(workspace_id),
            "project_id": str(project_id),
            "status": "queued",
            "template_id": "luxury_reel_v1",
            "render_engine": "ffmpeg_basic",
            "renderer_version": "renderer-test",
            "props_hash": "props-test",
            "artifact_path": None,
            "benchmark_ms": None,
            "logs": {
                "messages": ["render props prepared"],
                "compat": {
                    "uploaded_video_id": str(upload_id),
                    "caption_track_id": str(track_id),
                    "planner_version": "planner-test",
                    "manifest": {"source": {"bucket": "raw-uploads", "path": "video.mp4"}},
                    "keep_segments": [{"start_ms": 100, "end_ms": 900, "reason": "caption"}],
                    "specs": {"width": 1080, "height": 1920, "fps": 30, "format": "mp4"},
                    "progress_pct": 5,
                },
            },
            "error_code": None,
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
    )

    assert job.uploaded_video_id == upload_id
    assert job.caption_track_id == track_id
    assert job.planner_version == "planner-test"
    assert job.keep_segments[0].start_ms == 100
    assert job.specs.width == 1080
    assert job.progress_pct == 5
    assert job.logs == ["render props prepared"]


def test_supabase_render_status_writes_canonical_enum_and_reads_legacy_values() -> None:
    repository = make_supabase_render_repository()

    assert repository._db_status(RenderJobStatus.PREPARING) == "preparing"
    assert repository._db_status(RenderJobStatus.RENDERING) == "rendering"
    assert repository._db_status(RenderJobStatus.UPLOADING) == "uploading"
    assert repository._db_status(RenderJobStatus.COMPLETE) == "complete"
    assert repository._app_status("running", progress_pct=20) == RenderJobStatus.PREPARING
    assert repository._app_status("running", progress_pct=70) == RenderJobStatus.RENDERING
    assert repository._app_status("running", progress_pct=90) == RenderJobStatus.UPLOADING
    assert repository._app_status("succeeded") == RenderJobStatus.COMPLETE


def test_supabase_export_hydrates_from_live_schema_manifest() -> None:
    repository = make_supabase_render_repository()
    workspace_id = uuid4()
    project_id = uuid4()
    render_job_id = uuid4()
    export_id = uuid4()

    export = repository._export_from_row(
        {
            "id": str(export_id),
            "workspace_id": str(workspace_id),
            "project_id": str(project_id),
            "render_job_id": str(render_job_id),
            "version": 1,
            "storage_bucket": "exports",
            "storage_path": "workspaces/test/export.mp4",
            "manifest": {
                "compat_export": {
                    "format": "mp4",
                    "width": 1080,
                    "height": 1920,
                    "fps": 30,
                    "duration_ms": 800,
                    "file_size_bytes": 1234,
                }
            },
            "thumbnail_url": None,
            "settings_summary": {"template": "luxury_reel_v1"},
            "restored_from_export_id": None,
            "created_at": "2026-06-03T07:00:00+00:00",
        }
    )

    assert export.format == "mp4"
    assert export.width == 1080
    assert export.height == 1920
    assert export.duration_ms == 800
    assert export.file_size_bytes == 1234


def test_personal_use_render_skips_billing_limits_and_watermark() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request()
    request.app.state.settings.billing_enabled = False

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    assert response.status == "complete"
    assert response.job.specs.width == 1080
    assert response.job.specs.height == 1920
    assert response.job.manifest["billing"]["watermark"] is False


def test_render_rejects_cross_workspace_caption_track() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    track = repository.latest_caption_track(project_id=project_id, workspace_id=workspace_id)
    request = make_request()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            create_render(
                project_id=project_id,
                payload=RenderCreateRequest(caption_track_id=track.id),
                request=request,
                actor=Actor(user_id=user_id, workspace_id=uuid4()),
                repository=repository,
                billing_repository=InMemoryBillingRepository(),
                plan_repository=InMemoryPlanRepository(repository.caption_repository),
                render_queue=RenderQueue(),
            )
        )

    assert exc_info.value.status_code in {403, 404}


def test_render_stays_queued_when_worker_handoff_is_required() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request(complete_locally=False)

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )

    assert response.status == "queued"
    assert response.job.progress_pct == 5
    assert [event.to_status for event in response.job.events] == ["queued"]
    assert repository.list_exports(project_id=project_id, workspace_id=workspace_id) == []


def test_render_manifest_includes_music_selection_without_overwriting_caption_track() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(repository.caption_repository)
    music_repository = InMemoryMusicRepository(plan_repository)
    music_track = music_repository.upsert_track(
        workspace_id=workspace_id,
        project_id=project_id,
        provider=MusicProviderKey.FIXTURE,
        provider_track_id="fixture-ambient-luxury",
        title="Ambient Luxury Bed",
        license="royalty_free",
        duration_ms=68_000,
        mood_tags=["ambient"],
        bpm=80,
        energy=0.35,
        vocals=False,
        loopable=True,
        beat_map=[0.0, 750.0, 1500.0],
        raw_metadata={"source": "fixture"},
    )
    selection = music_repository.create_selection(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=uuid4(),
        status=MusicSelectionStatus.READY,
        music_track_id=music_track.id,
        music_volume=0.65,
        sfx_intensity=0.4,
        rights_confirmed=True,
        cache_key="test",
    )
    request = make_request_with_music(music_repository)

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=plan_repository,
            render_queue=RenderQueue(),
        )
    )

    assert response.job.caption_track_id
    assert response.job.manifest["music_selection_id"] == str(selection.id)
    music_props = response.job.manifest["render_props"]["musicSelection"]
    assert music_props["track"]["title"] == "Ambient Luxury Bed"
    assert music_props["mix"]["loudness_target_lufs"] == -14
    assert music_props["beat_alignment_report"]["enabled"] is True
    assert response.job.manifest["audio"]["music_volume"] == 0.65
    assert response.job.manifest["audio"]["sfx_intensity"] == 0.4
    exports = repository.list_exports(project_id=project_id, workspace_id=workspace_id)
    assert exports[0].settings_summary["music_selection_id"] == str(selection.id)


def test_render_manifest_includes_effects_config() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(repository.caption_repository)
    effects_repository = InMemoryEffectsRepository(plan_repository)
    config = effects_repository.create_config(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=None,
        status=EffectsStatus.READY,
        preset=VisualPresetKey.CINEMATIC_HOTEL,
        lut_intensity=0.85,
        texture=EffectsTextureSettings(
            grain=0.2,
            vignette=0.2,
            light_leak=0.1,
            anamorphic_flare=True,
        ),
        motion_effects=[MotionEffectKey.BROLL_SLIDE_IN, MotionEffectKey.GRADIENT_RAMP],
        background_removal=BackgroundRemovalSettings(enabled=True, apply_to_broll=True),
        gradient_ramp=GradientRampSettings(
            enabled=True, start_color="#111827", end_color="#f59e0b"
        ),
        after_effects_presets=[
            AfterEffectsPreset(name="Motionly Correction Controls", intensity=0.7)
        ],
        after_effects_project_url=None,
        correction_manifest_url=None,
        effect_count_limit_per_30s=4,
        render_time_impact="medium",
        preview_before_url=None,
        preview_after_url=None,
        cache_key="test",
    )
    request = make_request_with_effects(effects_repository)

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=plan_repository,
            render_queue=RenderQueue(),
        )
    )

    assert response.job.manifest["effects_config_id"] == str(config.id)
    effects_props = response.job.manifest["render_props"]["effectsConfig"]
    assert effects_props["preset"] == "cinematic_hotel"
    assert effects_props["lut_intensity"] == 0.85
    assert effects_props["background_removal"]["enabled"] is True
    assert effects_props["gradient_ramp"]["enabled"] is True
    assert response.job.manifest["external_finishing_metadata"]["project_file_url"] is None


def test_render_manifest_applies_motion_auto_reframe_to_export_framing() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(repository.caption_repository)
    effects_repository = InMemoryEffectsRepository(plan_repository)
    config = effects_repository.create_config(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=None,
        status=EffectsStatus.READY,
        preset=VisualPresetKey.CLEAN_FOUNDER,
        lut_intensity=0.72,
        texture=EffectsTextureSettings(),
        motion_effects=[],
        motion=MotionSettings(
            auto_reframe=AutoReframeSettings(
                enabled=True,
                target_aspect="9:16",
                subject_x_pct=68,
                subject_y_pct=42,
            )
        ),
        background_removal=BackgroundRemovalSettings(enabled=False),
        gradient_ramp=GradientRampSettings(enabled=False),
        after_effects_presets=[],
        after_effects_project_url=None,
        correction_manifest_url=None,
        effect_count_limit_per_30s=3,
        render_time_impact="medium",
        preview_before_url=None,
        preview_after_url=None,
        cache_key="motion-auto-reframe",
    )
    request = make_request_with_effects(effects_repository)

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=plan_repository,
            render_queue=RenderQueue(),
        )
    )

    assert response.job.manifest["effects_config_id"] == str(config.id)
    motion_design = response.job.manifest["motion_design"]
    assert motion_design["auto_reframe"]["applied"] is True
    framing = response.job.manifest["render_props"]["exportSettings"]["framing"]
    assert framing["fit_mode"] == "custom"
    assert framing["crop"]["x"] == 0.68
    assert framing["crop"]["y"] == 0.42
    assert response.job.manifest["export_settings"]["framing"] == framing


def test_render_manifest_includes_selected_broll_assets() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(repository.caption_repository)
    broll_repository = InMemoryBrollRepository(plan_repository)
    slot = seed_broll_slot(
        broll_repository=broll_repository,
        project_id=project_id,
        workspace_id=workspace_id,
    )

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=make_request(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=plan_repository,
            render_queue=RenderQueue(),
            broll_repository=broll_repository,
        )
    )

    manifest = response.job.manifest
    assert manifest["include_broll"] is True
    assert manifest["broll_selection_version"] == slot.version
    assert manifest["broll"]["active_asset_count"] == 1
    assert manifest["render_props"]["assets"][0]["source_start_ms"] == slot.start_ms
    assert manifest["render_props"]["assets"][0]["start_ms"] <= slot.start_ms
    assert manifest["render_props"]["assets"][0]["license"] == "pexels_license"


def test_render_manifest_rejects_private_stock_asset_url() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(repository.caption_repository)
    broll_repository = InMemoryBrollRepository(plan_repository)
    slot = seed_broll_slot(
        broll_repository=broll_repository,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    asset = broll_repository.get_asset(
        project_id=project_id,
        workspace_id=workspace_id,
        asset_id=slot.selected_asset_id,
    )
    asset.download_url = "http://169.254.169.254/latest/meta-data/"
    asset.preview_video_url = asset.download_url

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            create_render(
                project_id=project_id,
                payload=RenderCreateRequest(),
                request=make_request(complete_locally=False),
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                repository=repository,
                billing_repository=InMemoryBillingRepository(),
                plan_repository=plan_repository,
                render_queue=RenderQueue(),
                broll_repository=broll_repository,
            )
        )

    assert exc_info.value.code == "unsafe_media_url"
    assert "169.254" not in exc_info.value.message


def test_render_manifest_rejects_private_manual_audio_url() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(repository.caption_repository)
    from tests.worker_job_test_helpers import seed_active_plan

    seed_active_plan(
        plan_repository=plan_repository,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=PlanCreateRequest(target_duration_s=10, broll_density=BrollDensity.NONE),
    )
    plan = plan_repository.active_plan(project_id=project_id, workspace_id=workspace_id)
    plan.plan = plan.plan.model_copy(
        update={
            "manual_elements": [
                ManualElement(
                    id="unsafe-sfx",
                    type="sfx",
                    name="Unsafe SFX",
                    zone="timeline",
                    start_ms=0,
                    end_ms=7000,
                    properties={"assetUrl": "http://127.0.0.1/private.wav"},
                )
            ]
        }
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            create_render(
                project_id=project_id,
                payload=RenderCreateRequest(edit_plan_id=plan.id),
                request=make_request(complete_locally=False),
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                repository=repository,
                billing_repository=InMemoryBillingRepository(),
                plan_repository=plan_repository,
                render_queue=RenderQueue(),
            )
        )

    assert exc_info.value.code == "unsafe_media_url"
    assert "127.0.0.1" not in exc_info.value.message


def test_render_manifest_can_exclude_broll_assets() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    plan_repository = InMemoryPlanRepository(repository.caption_repository)
    broll_repository = InMemoryBrollRepository(plan_repository)
    seed_broll_slot(
        broll_repository=broll_repository,
        project_id=project_id,
        workspace_id=workspace_id,
    )

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(include_broll=False),
            request=make_request(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=plan_repository,
            render_queue=RenderQueue(),
            broll_repository=broll_repository,
        )
    )

    manifest = response.job.manifest
    assert manifest["include_broll"] is False
    assert manifest["broll"]["included"] is False
    assert manifest["broll"]["active_asset_count"] == 0
    assert manifest["render_props"]["assets"] == []


@pytest.mark.skipif(which("ffmpeg") is None or which("ffprobe") is None, reason="FFmpeg required")
def test_real_ffmpeg_export_includes_fixture_broll_music_sfx_and_effects(tmp_path) -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    _write_source_video(tmp_path / "raw-uploads" / "video.mp4")

    plan_repository = InMemoryPlanRepository(repository.caption_repository)
    broll_repository = InMemoryBrollRepository(plan_repository)
    seed_fixture_broll_slot(
        broll_repository=broll_repository,
        project_id=project_id,
        workspace_id=workspace_id,
    )

    music_repository = InMemoryMusicRepository(plan_repository)
    music_track = music_repository.upsert_track(
        workspace_id=workspace_id,
        project_id=project_id,
        provider=MusicProviderKey.FIXTURE,
        provider_track_id="fixture-clean-confident",
        title="Clean Confident Groove",
        license="royalty_free",
        duration_ms=73_000,
        mood_tags=["clean", "confident"],
        bpm=92,
        energy=0.55,
        vocals=False,
        loopable=True,
        beat_map=[0.0, 650.0, 1300.0, 1950.0],
        raw_metadata={"source": "fixture"},
    )
    music_selection = music_repository.create_selection(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=uuid4(),
        status=MusicSelectionStatus.READY,
        music_track_id=music_track.id,
        music_volume=0.35,
        sfx_intensity=0.5,
        rights_confirmed=True,
        cache_key="real-render-music",
    )

    effects_repository = InMemoryEffectsRepository(plan_repository)
    effects_config = effects_repository.create_config(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=None,
        status=EffectsStatus.READY,
        preset=VisualPresetKey.CHAMPAGNE_GOLD,
        lut_intensity=0.8,
        texture=EffectsTextureSettings(
            grain=0.12,
            vignette=0.1,
            light_leak=0.08,
            anamorphic_flare=True,
        ),
        motion_effects=[MotionEffectKey.FLASH],
        background_removal=BackgroundRemovalSettings(enabled=False),
        gradient_ramp=GradientRampSettings(
            enabled=True,
            start_color="#111827",
            end_color="#f59e0b",
            opacity=0.25,
        ),
        after_effects_presets=[],
        after_effects_project_url=None,
        correction_manifest_url=None,
        effect_count_limit_per_30s=4,
        render_time_impact="medium",
        preview_before_url=None,
        preview_after_url=None,
        cache_key="real-render-effects",
    )

    response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest.model_validate(
                {
                    "include_captions": True,
                    "include_broll": True,
                    "duration_preset": "short",
                    "export_options": {
                        "platform": "tiktok",
                        "resolution": "720p",
                        "format": "mp4",
                    },
                }
            ),
            request=make_real_render_request(
                tmp_path,
                music_repository=music_repository,
                effects_repository=effects_repository,
            ),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=plan_repository,
            render_queue=RenderQueue(),
            broll_repository=broll_repository,
        )
    )

    assert response.status == RenderJobStatus.COMPLETE
    manifest = response.job.manifest
    assert manifest["broll"]["active_asset_count"] == 1
    assert manifest["audio"]["music_selected"] is True
    assert manifest["music_selection_id"] == str(music_selection.id)
    assert manifest["effects_config_id"] == str(effects_config.id)

    export = repository.list_exports(project_id=project_id, workspace_id=workspace_id)[0]
    output_path = tmp_path / export.storage_bucket / export.storage_path
    assert output_path.exists()
    assert output_path.stat().st_size > 20_000
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name",
            "-show_entries",
            "format=duration",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "codec_name=h264" in probe.stdout
    assert "width=720" in probe.stdout
    assert "height=1280" in probe.stdout

    audio_probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "codec_name=aac" in audio_probe.stdout
    assert "broll_render_assets=1" in response.job.logs
    assert "music_mix=Clean Confident Groove" in response.job.logs
    assert "effects_config=champagne_gold:motion=1:gradient=1" in response.job.logs


def _write_source_video(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:sample_rate=44100:duration=6",
            "-shortest",
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
            str(path),
        ],
        check=True,
    )


def test_export_download_url_is_signed_after_workspace_check() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request()
    asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )
    export = repository.list_exports(project_id=project_id, workspace_id=workspace_id)[0]

    response = asyncio.run(
        get_export_download_url(
            export_id=export.id,
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )

    assert response.signed_download_url.startswith("https://storage.example.com/")


def test_restore_export_creates_new_version_without_deleting_source() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_request()
    asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(),
        )
    )
    source = repository.list_exports(project_id=project_id, workspace_id=workspace_id)[0]

    response = asyncio.run(
        restore_export_version(
            project_id=project_id,
            export_id=source.id,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
        )
    )

    exports = repository.list_exports(project_id=project_id, workspace_id=workspace_id)
    assert len(exports) == 2
    assert response.export.version == 2
    assert response.export.restored_from_export_id == source.id
    assert exports[1].id == source.id


def test_render_requires_ready_upload() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    upload = repository.get_active_upload(project_id=project_id, workspace_id=workspace_id)
    upload.status = UploadStatus.PROCESSING
    request = make_request()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            create_render(
                project_id=project_id,
                payload=RenderCreateRequest(),
                request=request,
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                repository=repository,
                billing_repository=InMemoryBillingRepository(),
                plan_repository=InMemoryPlanRepository(repository.caption_repository),
                render_queue=RenderQueue(),
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "source_video_not_ready"


def test_render_blocks_ready_upload_that_is_not_media_ready() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    upload = repository.get_active_upload(project_id=project_id, workspace_id=workspace_id)
    upload.metadata.has_audio = False
    request = make_request()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            create_render(
                project_id=project_id,
                payload=RenderCreateRequest(),
                request=request,
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                repository=repository,
                billing_repository=InMemoryBillingRepository(),
                plan_repository=InMemoryPlanRepository(repository.caption_repository),
                render_queue=RenderQueue(),
            )
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "video_not_render_ready"
    assert "No audio track was detected" in exc_info.value.message
