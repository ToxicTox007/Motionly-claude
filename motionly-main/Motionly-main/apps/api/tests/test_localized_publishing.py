import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.localized_publishing import (
    add_asset_collection_item,
    create_localized_publish_package,
    delete_asset_library_item,
    download_localized_subtitles,
    get_asset_library_media_url,
    index_project_assets,
    reuse_asset_in_project,
    review_localized_publish_package,
    update_asset_favorite,
)
from app.api.renders import create_render
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.localized_publishing import InMemoryLocalizedPublishingRepository
from app.repositories.music import InMemoryMusicRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.renders import InMemoryRenderRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.broll import StockOrientation, StockProviderKey
from app.schemas.captions import CaptionPresetKey, CaptionSegment, LanguageHint, TranscriptWord
from app.schemas.localized_publishing import (
    AssetCollectionItemRequest,
    AssetFavoriteUpdateRequest,
    AssetLibraryItemCreateRequest,
    AssetLibraryKind,
    AssetLibraryMediaState,
    LocalizedPublishPackageCreateRequest,
    LocalizedPublishPackageReviewRequest,
    LocalizedReviewStatus,
    PublishLanguage,
    SubtitleFormat,
)
from app.schemas.plans import ManualElement, PlanCreateRequest
from app.schemas.renders import RenderBulkCreateRequest, RenderCreateRequest
from app.schemas.uploads import UploadStatus
from app.services.asset_library_references import (
    AssetLibraryReferenceBinding,
    resolve_asset_library_reference,
)
from app.services.caption_presets import CAPTION_SCHEMA_VERSION, get_caption_preset
from app.services.model_gateway import PlanningOutput
from app.services.object_references import ObjectReference, ObjectReferenceService
from app.services.queue import RenderQueue
from app.services.storage import StorageSigner
from app.services.subtitles import subtitle_content
from tests.worker_job_test_helpers import seed_active_plan


class TranslationGateway:
    def invoke(self, request, *, context):
        source = json.loads(request.messages[-1].content)
        return SimpleNamespace(
            output=PlanningOutput(
                content=json.dumps(
                    {"segments": [f"FR {item}" for item in source["segments"]]},
                ),
                raw_response={},
            )
        )


def workspace_context():
    upload_repository = InMemoryUploadRepository()
    workspace_id, project_id, target_project_id, user_id = uuid4(), uuid4(), uuid4(), uuid4()
    upload_repository.seed_project(
        project_id=project_id, workspace_id=workspace_id, title="Build better"
    )
    upload_repository.seed_project(
        project_id=target_project_id, workspace_id=workspace_id, title="New reel"
    )
    upload = upload_repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path=(f"workspaces/{workspace_id}/projects/{project_id}/raw/source.mp4"),
        original_filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=2048,
    )
    upload.status = UploadStatus.READY
    upload.metadata.width = 1080
    upload.metadata.height = 1920
    upload.metadata.duration_ms = 12_000
    upload_repository.attach_active_video(project_id=project_id, video_id=upload.id)
    captions = InMemoryCaptionRepository(upload_repository)
    transcript = captions.create_transcript(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status="ready",
        language="en",
        language_hint=LanguageHint.ENGLISH,
        provider="fixture",
        model="fixture",
        confidence=0.99,
        duration_ms=12_000,
        raw_payload={},
        words=[
            TranscriptWord(index=0, text="Build", start_ms=0, end_ms=500),
            TranscriptWord(index=1, text="better", start_ms=510, end_ms=1000),
        ],
    )
    style = get_caption_preset(CaptionPresetKey.BOLD_INFLUENCER).style
    source_track = captions.create_caption_track(
        workspace_id=workspace_id,
        project_id=project_id,
        transcript_id=transcript.id,
        preset=CaptionPresetKey.BOLD_INFLUENCER,
        schema_version=CAPTION_SCHEMA_VERSION,
        style=style,
        hide_filler_words=False,
        language="en",
        review_status="approved",
        segments=[CaptionSegment(index=0, start_ms=0, end_ms=1000, text="Build better")],
    )
    plans = InMemoryPlanRepository(captions)
    seed_active_plan(
        plan_repository=plans,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=PlanCreateRequest(),
    )
    active = plans.active_plan(project_id=project_id, workspace_id=workspace_id)
    active.plan = active.plan.model_copy(
        update={
            "manual_elements": [
                ManualElement(
                    id="title-card",
                    type="text",
                    name="Title",
                    zone="canvas",
                    start_ms=0,
                    end_ms=1000,
                    properties={"content": "Build better"},
                )
            ]
        }
    )
    broll = InMemoryBrollRepository(plans)
    music = InMemoryMusicRepository(plans)
    renders = InMemoryRenderRepository(captions)
    localized = InMemoryLocalizedPublishingRepository()
    references = ObjectReferenceService(
        storage_signer=StorageSigner(base_url="http://api.local"),
        in_memory=True,
    )
    source_reference = asyncio.run(
        references.create_reference(
            workspace_id=workspace_id,
            project_id=project_id,
            storage_bucket=upload.storage_bucket,
            storage_path=upload.storage_path,
            owner_type="uploaded_video",
            owner_id=upload.id,
            object_type="source_video",
            mime_type=upload.mime_type,
            size_bytes=upload.file_size_bytes,
        )
    )
    upload.source_object_reference_id = source_reference.id
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                caption_repository=captions,
                plan_repository=plans,
                broll_repository=broll,
                music_repository=music,
                render_repository=renders,
                localized_publishing_repository=localized,
                object_reference_service=references,
                settings=SimpleNamespace(
                    upload_storage_bucket="raw-uploads",
                    export_storage_bucket="exports",
                ),
                model_gateway=TranslationGateway(),
            )
        )
    )
    return SimpleNamespace(
        request=request,
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        captions=captions,
        broll=broll,
        references=references,
        workspace_id=workspace_id,
        project_id=project_id,
        target_project_id=target_project_id,
        source_track=source_track,
    )


def test_localized_publish_package_keeps_source_track_and_exports_reviewed_subtitles() -> None:
    context = workspace_context()
    created = asyncio.run(
        create_localized_publish_package(
            project_id=context.project_id,
            payload=LocalizedPublishPackageCreateRequest(
                language=PublishLanguage.FRENCH,
                source_caption_track_id=context.source_track.id,
            ),
            request=context.request,
            actor=context.actor,
        )
    )

    assert created.review_status == LocalizedReviewStatus.NEEDS_REVIEW
    assert created.overlay_translations[0].content == "FR Build better"
    assert created.title_suggestions == ["FR Build better"]
    assert (
        context.captions.get_caption_track(
            project_id=context.project_id,
            track_id=context.source_track.id,
            workspace_id=context.workspace_id,
        )
        .segments[0]
        .text
        == "Build better"
    )
    translated = context.captions.get_caption_track(
        project_id=context.project_id,
        track_id=created.caption_track_id,
        workspace_id=context.workspace_id,
    )
    assert translated.language == "fr"
    assert translated.source_track_id == context.source_track.id
    assert translated.review_status == "needs_review"

    approved = asyncio.run(
        review_localized_publish_package(
            project_id=context.project_id,
            package_id=created.id,
            payload=LocalizedPublishPackageReviewRequest(
                review_status=LocalizedReviewStatus.APPROVED,
                selected_title=created.title_suggestions[0],
                selected_description=created.description_suggestions[0],
            ),
            request=context.request,
            actor=context.actor,
        )
    )
    assert approved.review_status == LocalizedReviewStatus.APPROVED
    reviewed_track = context.captions.get_caption_track(
        project_id=context.project_id,
        track_id=created.caption_track_id,
        workspace_id=context.workspace_id,
    )
    assert reviewed_track.review_status == "approved"
    assert reviewed_track.source_track_id == context.source_track.id
    download = asyncio.run(
        download_localized_subtitles(
            project_id=context.project_id,
            package_id=created.id,
            request=context.request,
            actor=context.actor,
            format=SubtitleFormat.SRT,
        )
    )
    assert "00:00:00,000 --> 00:00:01,000" in download.body.decode()
    assert "FR Build better" in download.body.decode()


def test_asset_library_indexes_favorites_collects_and_reuses_broll_between_projects() -> None:
    context = workspace_context()
    source_asset = context.broll.upsert_asset(
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        provider=StockProviderKey.USER_LIBRARY,
        provider_asset_id="source-broll",
        title="Studio cutaway",
        thumbnail_url=None,
        preview_video_url=None,
        download_url=None,
        license="user_owned",
        width=1080,
        height=1920,
        duration_ms=2000,
        orientation=StockOrientation.PORTRAIT,
        score=1,
        raw_metadata={
            "status": "ready",
            "storage_bucket": "raw-uploads",
            "storage_path": (
                f"workspaces/{context.workspace_id}/projects/{context.project_id}/broll/studio.mp4"
            ),
        },
    )
    indexed = asyncio.run(
        index_project_assets(
            project_id=context.project_id,
            request=context.request,
            actor=context.actor,
        )
    )
    broll_item = next(item for item in indexed.items if item.source_asset_id == source_asset.id)
    favorite = asyncio.run(
        update_asset_favorite(
            item_id=broll_item.id,
            payload=AssetFavoriteUpdateRequest(is_favorite=True),
            request=context.request,
            actor=context.actor,
        )
    )
    assert favorite.is_favorite is True
    collection = context.request.app.state.localized_publishing_repository.create_collection(
        workspace_id=context.workspace_id,
        name="Winning assets",
    )
    collected = asyncio.run(
        add_asset_collection_item(
            collection_id=collection.id,
            payload=AssetCollectionItemRequest(asset_id=broll_item.id),
            request=context.request,
            actor=context.actor,
        )
    )
    assert broll_item.id in collected.asset_ids
    assert broll_item.object_reference_id is not None
    assert broll_item.media_state == AssetLibraryMediaState.VERIFIED
    reused = asyncio.run(
        reuse_asset_in_project(
            item_id=broll_item.id,
            project_id=context.target_project_id,
            request=context.request,
            actor=context.actor,
        )
    )
    assert reused.status == "imported"
    target_asset = context.broll.get_asset(
        project_id=context.target_project_id,
        workspace_id=context.workspace_id,
        asset_id=reused.target_asset_id,
    )
    assert target_asset.raw_metadata["reused_library_item_id"] == str(broll_item.id)
    assert target_asset.raw_metadata["object_reference_id"] == str(broll_item.object_reference_id)
    removed = asyncio.run(
        delete_asset_library_item(
            item_id=broll_item.id,
            request=context.request,
            actor=context.actor,
        )
    )
    assert removed.status == "removed"
    with pytest.raises(ApiError, match="Asset library item was not found"):
        context.request.app.state.localized_publishing_repository.get_asset_item(
            item_id=broll_item.id,
            workspace_id=context.workspace_id,
        )


def test_asset_library_rejects_payload_authority_and_quarantines_inactive_media() -> None:
    context = workspace_context()
    with pytest.raises(ValidationError):
        AssetLibraryItemCreateRequest.model_validate(
            {
                "kind": "upload",
                "name": "forged source",
                "payload": {"storage_bucket": "attacker", "storage_path": "outside.mp4"},
            }
        )

    indexed = asyncio.run(
        index_project_assets(
            project_id=context.project_id,
            request=context.request,
            actor=context.actor,
        )
    )
    upload_item = next(item for item in indexed.items if item.kind.value == "upload")
    signed = asyncio.run(
        get_asset_library_media_url(
            item_id=upload_item.id,
            request=context.request,
            actor=context.actor,
        )
    )
    assert signed.object_reference_id == upload_item.object_reference_id
    assert "/raw-uploads/workspaces/" in signed.url
    assert f"/workspaces/{context.workspace_id}/projects/{context.project_id}/" in signed.url

    asyncio.run(
        context.references.mark_deleted(
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            reference_id=upload_item.object_reference_id,
        )
    )
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            reuse_asset_in_project(
                item_id=upload_item.id,
                project_id=context.target_project_id,
                request=context.request,
                actor=context.actor,
            )
        )
    assert exc_info.value.code == "asset_library_media_unavailable"
    quarantined = context.request.app.state.localized_publishing_repository.get_asset_item(
        item_id=upload_item.id,
        workspace_id=context.workspace_id,
    )
    assert quarantined.media_state == AssetLibraryMediaState.QUARANTINED
    assert quarantined.object_reference_id is None


def test_asset_library_media_policy_rejects_untrusted_bucket_prefix_and_project() -> None:
    context = workspace_context()
    upload = context.captions.get_active_upload(
        project_id=context.project_id,
        workspace_id=context.workspace_id,
    )
    base = {
        "workspace_id": context.workspace_id,
        "source_project_id": context.project_id,
        "source_asset_id": upload.id,
        "kind": AssetLibraryKind.UPLOAD,
        "media_state": AssetLibraryMediaState.VERIFIED,
    }
    forbidden_bucket = asyncio.run(
        context.references.create_reference(
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            storage_bucket="untrusted-bucket",
            storage_path=(
                f"workspaces/{context.workspace_id}/projects/{context.project_id}/raw/other.mp4"
            ),
            owner_type="uploaded_video",
            owner_id=upload.id,
            object_type="source_video",
        )
    )
    with pytest.raises(ApiError) as bucket_error:
        asyncio.run(
            resolve_asset_library_reference(
                binding=AssetLibraryReferenceBinding(
                    object_reference_id=forbidden_bucket.id,
                    **base,
                ),
                object_reference_service=context.references,
                allowed_buckets={"raw-uploads", "exports"},
            )
        )
    assert bucket_error.value.code == "asset_library_media_invalid"

    invalid_prefix_id = uuid4()
    context.references._references[invalid_prefix_id] = ObjectReference(  # noqa: SLF001
        id=invalid_prefix_id,
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        storage_bucket="raw-uploads",
        storage_path="legacy/unproven/source.mp4",
        owner_type="uploaded_video",
        owner_id=upload.id,
        object_type="source_video",
        mime_type="video/mp4",
        size_bytes=1,
        sha256=None,
    )
    with pytest.raises(ApiError) as prefix_error:
        asyncio.run(
            resolve_asset_library_reference(
                binding=AssetLibraryReferenceBinding(
                    object_reference_id=invalid_prefix_id,
                    **base,
                ),
                object_reference_service=context.references,
                allowed_buckets={"raw-uploads", "exports"},
            )
        )
    assert prefix_error.value.code == "asset_library_media_invalid"

    foreign_project_reference = asyncio.run(
        context.references.create_reference(
            workspace_id=context.workspace_id,
            project_id=context.target_project_id,
            storage_bucket="raw-uploads",
            storage_path=(
                f"workspaces/{context.workspace_id}/projects/{context.target_project_id}/raw/foreign.mp4"
            ),
            owner_type="uploaded_video",
            owner_id=upload.id,
            object_type="source_video",
        )
    )
    with pytest.raises(ApiError) as tenant_error:
        asyncio.run(
            resolve_asset_library_reference(
                binding=AssetLibraryReferenceBinding(
                    object_reference_id=foreign_project_reference.id,
                    **base,
                ),
                object_reference_service=context.references,
                allowed_buckets={"raw-uploads", "exports"},
            )
        )
    assert tenant_error.value.code == "asset_library_media_unavailable"


def test_subtitle_serialization_is_deterministic_for_srt_and_webvtt() -> None:
    segments = [
        CaptionSegment(index=0, start_ms=0, end_ms=1_250, text="First line"),
        CaptionSegment(index=1, start_ms=62_345, end_ms=63_000, text="Second line"),
    ]

    assert subtitle_content(segments=segments, format=SubtitleFormat.SRT) == (
        "1\n00:00:00,000 --> 00:00:01,250\nFirst line\n\n"
        "2\n00:01:02,345 --> 00:01:03,000\nSecond line\n"
    )
    assert subtitle_content(segments=segments, format=SubtitleFormat.VTT) == (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.250\nFirst line\n\n"
        "00:01:02.345 --> 00:01:03.000\nSecond line\n"
    )


def test_render_requires_approval_then_uses_reviewed_locale_copy() -> None:
    context = workspace_context()
    package = asyncio.run(
        create_localized_publish_package(
            project_id=context.project_id,
            payload=LocalizedPublishPackageCreateRequest(
                language=PublishLanguage.FRENCH,
                source_caption_track_id=context.source_track.id,
            ),
            request=context.request,
            actor=context.actor,
        )
    )
    upload = context.captions.get_active_upload(
        project_id=context.project_id,
        workspace_id=context.workspace_id,
    )
    upload.source_object_reference_id = uuid4()
    upload.metadata.fps = 30
    upload.metadata.codec = "h264"
    upload.metadata.has_audio = True
    context.request.app.state.settings = SimpleNamespace(
        billing_enabled=False,
        export_storage_bucket="exports",
        export_signed_url_ttl_seconds=300,
        should_complete_renders_synchronously=False,
        media_work_dir=".motionly/test-storage",
        remotion_enabled=False,
    )
    render_kwargs = {
        "project_id": context.project_id,
        "payload": RenderCreateRequest(
            localized_publish_package_id=package.id,
            edit_plan_id=context.request.app.state.plan_repository.active_plan(
                project_id=context.project_id,
                workspace_id=context.workspace_id,
            ).id,
        ),
        "request": context.request,
        "actor": context.actor,
        "repository": context.request.app.state.render_repository,
        "billing_repository": InMemoryBillingRepository(),
        "plan_repository": context.request.app.state.plan_repository,
        "render_queue": RenderQueue(),
    }

    with pytest.raises(ApiError, match="Review and approve") as exc_info:
        asyncio.run(create_render(**render_kwargs))
    assert exc_info.value.code == "localized_publish_review_required"

    asyncio.run(
        review_localized_publish_package(
            project_id=context.project_id,
            package_id=package.id,
            payload=LocalizedPublishPackageReviewRequest(
                review_status=LocalizedReviewStatus.APPROVED,
            ),
            request=context.request,
            actor=context.actor,
        )
    )
    response = asyncio.run(create_render(**render_kwargs))
    render_props = response.job.manifest["render_props"]
    assert response.job.manifest["caption_track_id"] == str(package.caption_track_id)
    assert render_props["captions"][0]["text"] == "FR Build better"
    assert (
        render_props["editPlan"]["manual_elements"][0]["properties"]["content"] == "FR Build better"
    )
    assert (
        context.request.app.state.plan_repository.active_plan(
            project_id=context.project_id,
            workspace_id=context.workspace_id,
        )
        .plan.manual_elements[0]
        .properties["content"]
        == "Build better"
    )


def test_bulk_render_request_accepts_localized_package_id() -> None:
    package_id = uuid4()
    request = RenderBulkCreateRequest.model_validate(
        {
            "localized_publish_package_id": str(package_id),
            "export_targets": [{"platform": "instagram", "format": "mp4"}],
        }
    )
    assert request.localized_publish_package_id == package_id
