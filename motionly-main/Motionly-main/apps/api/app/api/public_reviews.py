from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth import enforce_hourly_limit
from app.api.dependencies import get_review_repository
from app.api.errors import ApiError
from app.repositories.reviews import InMemoryReviewRepository, ResolvedShareLink
from app.schemas.reviews import (
    PublicReviewCommentCreateRequest,
    PublicReviewCommentResponse,
    PublicReviewDecisionRequest,
    PublicReviewResolveRequest,
    PublicReviewResponse,
)
from app.services.object_references import ObjectReferenceService
from app.services.offload import run_blocking

router = APIRouter(prefix="/public/reviews", tags=["public client reviews"])

ReviewRepositoryDep = Annotated[InMemoryReviewRepository, Depends(get_review_repository)]


async def _repository_call(repository: Any, async_name: str, sync_name: str, **kwargs: Any):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return await run_blocking(getattr(repository, sync_name), **kwargs)


async def _limit(request: Request) -> None:
    settings = request.app.state.settings
    await enforce_hourly_limit(
        request,
        action="public_review",
        per_viewer=getattr(settings, "public_review_rate_limit_per_hour", 240),
        overall=getattr(settings, "public_review_global_rate_limit_per_hour", 10000),
    )


async def _resolve(*, repository: Any, share_link_id: UUID, token: str) -> ResolvedShareLink:
    try:
        return await _repository_call(
            repository,
            "resolve_share_link_async",
            "resolve_share_link",
            share_link_id=share_link_id,
            token=token,
        )
    except ApiError as exc:
        # Keep all capability lookup failures indistinguishable to prevent
        # link-id or expiry-state enumeration.
        if exc.status_code in {403, 404}:
            raise ApiError(404, "review_link_not_found", "Review link was not found.") from exc
        raise


async def _public_response(
    *,
    resolved: ResolvedShareLink,
    repository: Any,
    request: Request,
) -> PublicReviewResponse:
    comments = await _repository_call(
        repository,
        "list_comments_async",
        "list_comments",
        review_id=resolved.review.id,
        workspace_id=resolved.link.workspace_id,
        project_id=resolved.link.project_id,
    )
    media_url = None
    media_expires_at: datetime | None = None
    try:
        object_references: ObjectReferenceService = request.app.state.object_reference_service
        signed = await object_references.get_share_link_signed_url(
            workspace_id=resolved.link.workspace_id,
            project_id=resolved.link.project_id,
            reference_id=resolved.review.source_object_reference_id,
            share_link_id=resolved.link.id,
            link_expires_at=resolved.link.expires_at,
        )
        media_url = signed.url
        media_expires_at = signed.expires_at
    except ApiError:
        # A deleted or superseded source simply removes public preview access;
        # it must not disclose tenant or storage information to the link holder.
        pass
    return PublicReviewResponse(
        id=resolved.review.id,
        edit_plan_revision=resolved.review.edit_plan_revision,
        source_media_revision=resolved.review.source_media_revision,
        plan_hash=resolved.review.plan_hash,
        status=resolved.review.status,
        status_revision=resolved.review.status_revision,
        decision_reason=resolved.review.decision_reason,
        decided_at=resolved.review.decided_at,
        created_at=resolved.review.created_at,
        comments=[
            PublicReviewCommentResponse(
                id=comment.id,
                parent_comment_id=comment.parent_comment_id,
                timecode_ms=comment.timecode_ms,
                body=comment.body,
                author_name=comment.author_name or "Reviewer",
                created_at=comment.created_at,
                updated_at=comment.updated_at,
            )
            for comment in comments
        ],
        can_comment=resolved.link.can_comment,
        can_decide=resolved.link.can_decide,
        expires_at=resolved.link.expires_at,
        media_url=media_url,
        media_expires_at=media_expires_at,
    )


@router.post("/{share_link_id}/resolve", response_model=PublicReviewResponse)
async def resolve_public_review(
    share_link_id: UUID,
    payload: PublicReviewResolveRequest,
    response: Response,
    request: Request,
    repository: ReviewRepositoryDep,
) -> PublicReviewResponse:
    response.headers["Cache-Control"] = "no-store, private"
    await _limit(request)
    resolved = await _resolve(
        repository=repository, share_link_id=share_link_id, token=payload.token
    )
    return await _public_response(
        resolved=resolved,
        repository=repository,
        request=request,
    )


@router.post(
    "/{share_link_id}/comments",
    response_model=PublicReviewCommentResponse,
    status_code=201,
)
async def create_public_review_comment(
    share_link_id: UUID,
    payload: PublicReviewCommentCreateRequest,
    response: Response,
    repository: ReviewRepositoryDep,
    request: Request = None,  # type: ignore[assignment]
) -> PublicReviewCommentResponse:
    response.headers["Cache-Control"] = "no-store, private"
    if request is not None:
        await _limit(request)
    resolved = await _resolve(
        repository=repository, share_link_id=share_link_id, token=payload.token
    )
    if not resolved.link.can_comment:
        raise ApiError(
            403, "review_link_comment_forbidden", "Comments are disabled for this review link."
        )
    comment = await _repository_call(
        repository,
        "create_comment_async",
        "create_comment",
        review_id=resolved.review.id,
        workspace_id=resolved.link.workspace_id,
        project_id=resolved.link.project_id,
        body=payload.body,
        timecode_ms=payload.timecode_ms,
        parent_comment_id=payload.parent_comment_id,
        assignee_user_ids=[],
        author_user_id=None,
        author_name=payload.author_name,
    )
    return PublicReviewCommentResponse(
        id=comment.id,
        parent_comment_id=comment.parent_comment_id,
        timecode_ms=comment.timecode_ms,
        body=comment.body,
        author_name=comment.author_name or "Reviewer",
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


@router.post("/{share_link_id}/decision", response_model=PublicReviewResponse)
async def decide_public_review(
    share_link_id: UUID,
    payload: PublicReviewDecisionRequest,
    response: Response,
    request: Request,
    repository: ReviewRepositoryDep,
) -> PublicReviewResponse:
    response.headers["Cache-Control"] = "no-store, private"
    await _limit(request)
    resolved = await _resolve(
        repository=repository, share_link_id=share_link_id, token=payload.token
    )
    if not resolved.link.can_decide:
        raise ApiError(
            403, "review_link_decision_forbidden", "Decisions are disabled for this review link."
        )
    review = await _repository_call(
        repository,
        "decide_async",
        "decide",
        review_id=resolved.review.id,
        workspace_id=resolved.link.workspace_id,
        project_id=resolved.link.project_id,
        decision=payload.decision,
        expected_status_revision=payload.expected_status_revision,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        decided_by=None,
    )
    return await _public_response(
        resolved=ResolvedShareLink(link=resolved.link, review=review),
        repository=repository,
        request=request,
    )
