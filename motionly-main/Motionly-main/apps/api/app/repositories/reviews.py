from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.reviews import (
    ReviewCommentResponse,
    ReviewDetailResponse,
    ReviewResponse,
    ReviewStatus,
)
from app.services.ai_proposals import canonical_hash
from app.services.supabase import SupabaseRestClient

REVIEW_COLUMNS = (
    "id,workspace_id,project_id,edit_plan_id,edit_plan_revision,uploaded_video_id,"
    "source_media_revision,source_object_reference_id,source_sha256,"
    "plan_hash,status,status_revision,decision_reason,decision_idempotency_key,decided_by,"
    "decided_at,created_by,created_at,updated_at"
)
COMMENT_COLUMNS = (
    "id,workspace_id,project_id,review_id,parent_comment_id,timecode_ms,body,author_user_id,"
    "author_name,created_at,updated_at,deleted_at"
)
LINK_COLUMNS = (
    "id,workspace_id,project_id,review_id,token_digest,can_comment,can_decide,expires_at,"
    "revoked_at,created_by,last_accessed_at,created_at"
)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass
class ReviewRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    edit_plan_id: UUID
    edit_plan_revision: int
    uploaded_video_id: UUID
    source_media_revision: int
    source_object_reference_id: UUID
    source_sha256: str
    plan_snapshot: dict[str, Any]
    plan_hash: str
    status: ReviewStatus = ReviewStatus.OPEN
    status_revision: int = 1
    decision_reason: str | None = None
    decision_idempotency_key: str | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    created_by: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> ReviewResponse:
        return ReviewResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            edit_plan_id=self.edit_plan_id,
            edit_plan_revision=self.edit_plan_revision,
            uploaded_video_id=self.uploaded_video_id,
            source_media_revision=self.source_media_revision,
            source_object_reference_id=self.source_object_reference_id,
            source_sha256=self.source_sha256,
            plan_hash=self.plan_hash,
            status=self.status,
            status_revision=self.status_revision,
            decision_reason=self.decision_reason,
            decided_by=self.decided_by,
            decided_at=self.decided_at,
            created_by=self.created_by,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class ReviewCommentRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    review_id: UUID
    body: str
    author_user_id: UUID | None
    author_name: str | None
    timecode_ms: int | None = None
    parent_comment_id: UUID | None = None
    assignee_user_ids: list[UUID] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> ReviewCommentResponse:
        return ReviewCommentResponse(
            id=self.id,
            review_id=self.review_id,
            parent_comment_id=self.parent_comment_id,
            timecode_ms=self.timecode_ms,
            body=self.body,
            author_user_id=self.author_user_id,
            author_name=self.author_name,
            assignee_user_ids=self.assignee_user_ids,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class ReviewShareLinkRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    review_id: UUID
    token_digest: str
    can_comment: bool
    can_decide: bool
    expires_at: datetime
    created_by: UUID
    revoked_at: datetime | None = None
    last_accessed_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResolvedShareLink:
    link: ReviewShareLinkRecord
    review: ReviewRecord


class InMemoryReviewRepository:
    def __init__(self) -> None:
        self.reviews: dict[UUID, ReviewRecord] = {}
        self.comments: dict[UUID, ReviewCommentRecord] = {}
        self.share_links: dict[UUID, ReviewShareLinkRecord] = {}
        self.active_members: set[tuple[UUID, UUID]] = set()
        self._lock = RLock()

    def set_member_status(self, *, workspace_id: UUID, user_id: UUID, status: str) -> None:
        """Test-only membership state that mirrors production assignment checks."""
        key = (workspace_id, user_id)
        if status == "active":
            self.active_members.add(key)
        else:
            self.active_members.discard(key)

    def set_active_member(self, *, workspace_id: UUID, user_id: UUID) -> None:
        self.set_member_status(workspace_id=workspace_id, user_id=user_id, status="active")

    def create_review(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        edit_plan_id: UUID,
        edit_plan_revision: int,
        uploaded_video_id: UUID,
        source_media_revision: int,
        source_object_reference_id: UUID,
        source_sha256: str,
        plan_snapshot: dict[str, Any],
        created_by: UUID,
    ) -> ReviewRecord:
        review = ReviewRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            edit_plan_id=edit_plan_id,
            edit_plan_revision=edit_plan_revision,
            uploaded_video_id=uploaded_video_id,
            source_media_revision=source_media_revision,
            source_object_reference_id=source_object_reference_id,
            source_sha256=source_sha256,
            plan_snapshot=plan_snapshot,
            plan_hash=canonical_hash(plan_snapshot),
            created_by=created_by,
        )
        with self._lock:
            self.reviews[review.id] = review
        return review

    def list_reviews(
        self, *, workspace_id: UUID, project_id: UUID, limit: int = 50
    ) -> list[ReviewRecord]:
        return sorted(
            [
                review
                for review in self.reviews.values()
                if review.workspace_id == workspace_id and review.project_id == project_id
            ],
            key=lambda review: review.created_at,
            reverse=True,
        )[:limit]

    def get_review(self, *, review_id: UUID, workspace_id: UUID, project_id: UUID) -> ReviewRecord:
        review = self.reviews.get(review_id)
        if review is None or review.workspace_id != workspace_id or review.project_id != project_id:
            raise ApiError(404, "review_not_found", "Client review was not found.")
        return review

    def list_comments(
        self, *, review_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> list[ReviewCommentRecord]:
        self.get_review(review_id=review_id, workspace_id=workspace_id, project_id=project_id)
        return sorted(
            [
                comment
                for comment in self.comments.values()
                if comment.review_id == review_id
                and comment.workspace_id == workspace_id
                and comment.project_id == project_id
            ],
            key=lambda comment: (
                comment.timecode_ms is None,
                comment.timecode_ms or 0,
                comment.created_at,
            ),
        )

    def get_detail(
        self, *, review_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> ReviewDetailResponse:
        review = self.get_review(
            review_id=review_id, workspace_id=workspace_id, project_id=project_id
        )
        return ReviewDetailResponse(
            **review.to_response().model_dump(),
            comments=[
                comment.to_response()
                for comment in self.list_comments(
                    review_id=review_id, workspace_id=workspace_id, project_id=project_id
                )
            ],
        )

    def create_comment(
        self,
        *,
        review_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        body: str,
        timecode_ms: int | None,
        parent_comment_id: UUID | None,
        assignee_user_ids: list[UUID],
        author_user_id: UUID | None,
        author_name: str | None,
    ) -> ReviewCommentRecord:
        self.get_review(review_id=review_id, workspace_id=workspace_id, project_id=project_id)
        if any((workspace_id, user_id) not in self.active_members for user_id in assignee_user_ids):
            raise ApiError(
                422,
                "review_assignee_invalid",
                "Assignees must be active members of this workspace.",
            )
        if parent_comment_id is not None:
            parent = self.comments.get(parent_comment_id)
            if parent is None or parent.review_id != review_id:
                raise ApiError(422, "review_comment_parent_invalid", "Parent comment is invalid.")
        comment = ReviewCommentRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            review_id=review_id,
            body=body,
            author_user_id=author_user_id,
            author_name=author_name,
            timecode_ms=timecode_ms,
            parent_comment_id=parent_comment_id,
            assignee_user_ids=assignee_user_ids,
        )
        with self._lock:
            self.comments[comment.id] = comment
        return comment

    def decide(
        self,
        *,
        review_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        decision: ReviewStatus,
        expected_status_revision: int,
        reason: str | None,
        idempotency_key: str,
        decided_by: UUID | None,
    ) -> ReviewRecord:
        if decision == ReviewStatus.OPEN:
            raise ApiError(
                422, "review_decision_invalid", "Review decisions must approve or reject."
            )
        with self._lock:
            review = self.get_review(
                review_id=review_id, workspace_id=workspace_id, project_id=project_id
            )
            if review.status != ReviewStatus.OPEN:
                if review.decision_idempotency_key == idempotency_key:
                    return review
                raise ApiError(
                    409, "review_decision_conflict", "This review already has a decision."
                )
            if review.status_revision != expected_status_revision:
                raise ApiError(
                    409, "review_status_stale", "This review changed before it was decided."
                )
            now = datetime.now(UTC)
            review.status = decision
            review.status_revision += 1
            review.decision_reason = reason
            review.decision_idempotency_key = idempotency_key
            review.decided_by = decided_by
            review.decided_at = now
            review.updated_at = now
            return review

    def create_share_link(
        self,
        *,
        review_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        token: str,
        expires_at: datetime,
        can_comment: bool,
        can_decide: bool,
        created_by: UUID,
    ) -> ReviewShareLinkRecord:
        self.get_review(review_id=review_id, workspace_id=workspace_id, project_id=project_id)
        link = ReviewShareLinkRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            review_id=review_id,
            token_digest=token_digest(token),
            can_comment=can_comment,
            can_decide=can_decide,
            expires_at=expires_at,
            created_by=created_by,
        )
        with self._lock:
            self.share_links[link.id] = link
        return link

    def resolve_share_link(self, *, share_link_id: UUID, token: str) -> ResolvedShareLink:
        link = self.share_links.get(share_link_id)
        now = datetime.now(UTC)
        if (
            link is None
            or link.token_digest != token_digest(token)
            or link.revoked_at is not None
            or link.expires_at <= now
        ):
            raise ApiError(404, "review_link_not_found", "Review link was not found.")
        review = self.get_review(
            review_id=link.review_id,
            workspace_id=link.workspace_id,
            project_id=link.project_id,
        )
        link.last_accessed_at = now
        return ResolvedShareLink(link=link, review=review)


class SupabaseReviewRepository(InMemoryReviewRepository):
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: object | None = None,
    ) -> None:
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="reviews",
            metrics_registry=metrics_registry,
        )

    def create_review(self, **values: Any) -> ReviewRecord:
        plan_snapshot = dict(values["plan_snapshot"])
        payload = {
            "workspace_id": str(values["workspace_id"]),
            "project_id": str(values["project_id"]),
            "edit_plan_id": str(values["edit_plan_id"]),
            "edit_plan_revision": values["edit_plan_revision"],
            "uploaded_video_id": str(values["uploaded_video_id"]),
            "source_media_revision": values["source_media_revision"],
            "source_object_reference_id": str(values["source_object_reference_id"]),
            "source_sha256": values["source_sha256"],
            "plan_snapshot": plan_snapshot,
            "plan_hash": canonical_hash(plan_snapshot),
            "created_by": str(values["created_by"]),
        }
        response = self.client.request(
            "POST", "/client_review_revisions", params={"select": REVIEW_COLUMNS}, json=payload
        )
        self._raise(response)
        return self._review_from_row(response.json()[0])

    def list_reviews(
        self, *, workspace_id: UUID, project_id: UUID, limit: int = 50
    ) -> list[ReviewRecord]:
        response = self.client.request(
            "GET",
            "/client_review_revisions",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": REVIEW_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        self._raise(response)
        return [self._review_from_row(row) for row in response.json()]

    def get_review(self, *, review_id: UUID, workspace_id: UUID, project_id: UUID) -> ReviewRecord:
        response = self.client.request(
            "GET",
            "/client_review_revisions",
            params={
                "id": f"eq.{review_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": REVIEW_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "review_not_found", "Client review was not found.")
        return self._review_from_row(rows[0])

    def list_comments(
        self, *, review_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> list[ReviewCommentRecord]:
        self.get_review(review_id=review_id, workspace_id=workspace_id, project_id=project_id)
        response = self.client.request(
            "GET",
            "/client_review_comments",
            params={
                "review_id": f"eq.{review_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "deleted_at": "is.null",
                "select": COMMENT_COLUMNS,
                "order": "timecode_ms.asc.nullslast,created_at.asc,id.asc",
                "limit": "500",
            },
        )
        self._raise(response)
        comments = [self._comment_from_row(row) for row in response.json()]
        if not comments:
            return []
        ids = ",".join(str(comment.id) for comment in comments)
        assignments = self.client.request(
            "GET",
            "/client_review_comment_assignees",
            params={"comment_id": f"in.({ids})", "select": "comment_id,user_id", "limit": "10000"},
        )
        self._raise(assignments)
        by_comment: dict[UUID, list[UUID]] = {comment.id: [] for comment in comments}
        for row in assignments.json():
            by_comment[UUID(str(row["comment_id"]))].append(UUID(str(row["user_id"])))
        for comment in comments:
            comment.assignee_user_ids = by_comment[comment.id]
        return comments

    def create_comment(self, **values: Any) -> ReviewCommentRecord:
        self.get_review(
            review_id=values["review_id"],
            workspace_id=values["workspace_id"],
            project_id=values["project_id"],
        )
        assignees = list(values["assignee_user_ids"])
        self._assert_active_assignees(workspace_id=values["workspace_id"], user_ids=assignees)
        if values["parent_comment_id"] is not None:
            parent = self._get_comment(
                comment_id=values["parent_comment_id"],
                review_id=values["review_id"],
                workspace_id=values["workspace_id"],
                project_id=values["project_id"],
            )
            if parent is None:
                raise ApiError(422, "review_comment_parent_invalid", "Parent comment is invalid.")
        response = self.client.request(
            "POST",
            "/client_review_comments",
            params={"select": COMMENT_COLUMNS},
            json={
                "workspace_id": str(values["workspace_id"]),
                "project_id": str(values["project_id"]),
                "review_id": str(values["review_id"]),
                "body": values["body"],
                "timecode_ms": values["timecode_ms"],
                "parent_comment_id": str(values["parent_comment_id"])
                if values["parent_comment_id"]
                else None,
                "author_user_id": str(values["author_user_id"])
                if values["author_user_id"]
                else None,
                "author_name": values["author_name"],
            },
        )
        self._raise(response)
        comment = self._comment_from_row(response.json()[0])
        if assignees:
            assignment_response = self.client.request(
                "POST",
                "/client_review_comment_assignees",
                json=[
                    {
                        "workspace_id": str(values["workspace_id"]),
                        "project_id": str(values["project_id"]),
                        "review_id": str(values["review_id"]),
                        "comment_id": str(comment.id),
                        "user_id": str(user_id),
                        "assigned_by": str(values["author_user_id"])
                        if values["author_user_id"]
                        else None,
                    }
                    for user_id in assignees
                ],
            )
            self._raise(assignment_response)
            comment.assignee_user_ids = assignees
        return comment

    def decide(self, **values: Any) -> ReviewRecord:
        review = self.get_review(
            review_id=values["review_id"],
            workspace_id=values["workspace_id"],
            project_id=values["project_id"],
        )
        if review.status != ReviewStatus.OPEN:
            if review.decision_idempotency_key == values["idempotency_key"]:
                return review
            raise ApiError(409, "review_decision_conflict", "This review already has a decision.")
        if review.status_revision != values["expected_status_revision"]:
            raise ApiError(409, "review_status_stale", "This review changed before it was decided.")
        now = datetime.now(UTC)
        response = self.client.request(
            "PATCH",
            "/client_review_revisions",
            params={
                "id": f"eq.{review.id}",
                "workspace_id": f"eq.{review.workspace_id}",
                "project_id": f"eq.{review.project_id}",
                "status": "eq.open",
                "status_revision": f"eq.{review.status_revision}",
                "select": REVIEW_COLUMNS,
            },
            json={
                "status": values["decision"].value,
                "status_revision": review.status_revision + 1,
                "decision_reason": values["reason"],
                "decision_idempotency_key": values["idempotency_key"],
                "decided_by": str(values["decided_by"]) if values["decided_by"] else None,
                "decided_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(409, "review_status_stale", "This review changed before it was decided.")
        return self._review_from_row(rows[0])

    def create_share_link(self, **values: Any) -> ReviewShareLinkRecord:
        self.get_review(
            review_id=values["review_id"],
            workspace_id=values["workspace_id"],
            project_id=values["project_id"],
        )
        response = self.client.request(
            "POST",
            "/client_review_share_links",
            params={"select": LINK_COLUMNS},
            json={
                "workspace_id": str(values["workspace_id"]),
                "project_id": str(values["project_id"]),
                "review_id": str(values["review_id"]),
                "token_digest": token_digest(values["token"]),
                "can_comment": values["can_comment"],
                "can_decide": values["can_decide"],
                "expires_at": values["expires_at"].isoformat(),
                "created_by": str(values["created_by"]),
            },
        )
        self._raise(response)
        return self._link_from_row(response.json()[0])

    def resolve_share_link(self, *, share_link_id: UUID, token: str) -> ResolvedShareLink:
        now = datetime.now(UTC)
        response = self.client.request(
            "GET",
            "/client_review_share_links",
            params={
                "id": f"eq.{share_link_id}",
                "token_digest": f"eq.{token_digest(token)}",
                "revoked_at": "is.null",
                "expires_at": f"gt.{now.isoformat()}",
                "select": LINK_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "review_link_not_found", "Review link was not found.")
        link = self._link_from_row(rows[0])
        review = self.get_review(
            review_id=link.review_id,
            workspace_id=link.workspace_id,
            project_id=link.project_id,
        )
        access = self.client.request(
            "PATCH",
            "/client_review_share_links",
            params={"id": f"eq.{link.id}"},
            json={"last_accessed_at": now.isoformat()},
        )
        self._raise(access)
        return ResolvedShareLink(link=link, review=review)

    def _assert_active_assignees(self, *, workspace_id: UUID, user_ids: list[UUID]) -> None:
        if not user_ids:
            return
        joined = ",".join(str(user_id) for user_id in user_ids)
        response = self.client.request(
            "GET",
            "/workspace_members",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "user_id": f"in.({joined})",
                "status": "eq.active",
                "select": "user_id",
                "limit": str(len(user_ids)),
            },
        )
        self._raise(response)
        actual = {UUID(str(row["user_id"])) for row in response.json()}
        if actual != set(user_ids):
            raise ApiError(
                422,
                "review_assignee_invalid",
                "Assignees must be active members of this workspace.",
            )

    def _get_comment(
        self, *, comment_id: UUID, review_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> ReviewCommentRecord | None:
        response = self.client.request(
            "GET",
            "/client_review_comments",
            params={
                "id": f"eq.{comment_id}",
                "review_id": f"eq.{review_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "deleted_at": "is.null",
                "select": COMMENT_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        return self._comment_from_row(rows[0]) if rows else None

    @staticmethod
    def _review_from_row(row: dict[str, Any]) -> ReviewRecord:
        return ReviewRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            edit_plan_id=UUID(str(row["edit_plan_id"])),
            edit_plan_revision=int(row["edit_plan_revision"]),
            uploaded_video_id=UUID(str(row["uploaded_video_id"])),
            source_media_revision=int(row["source_media_revision"]),
            source_object_reference_id=UUID(str(row["source_object_reference_id"])),
            source_sha256=str(row["source_sha256"]),
            plan_snapshot=dict(row.get("plan_snapshot") or {}),
            plan_hash=str(row["plan_hash"]),
            status=ReviewStatus(str(row["status"])),
            status_revision=int(row["status_revision"]),
            decision_reason=row.get("decision_reason"),
            decision_idempotency_key=row.get("decision_idempotency_key"),
            decided_by=UUID(str(row["decided_by"])) if row.get("decided_by") else None,
            decided_at=_datetime(row["decided_at"]) if row.get("decided_at") else None,
            created_by=UUID(str(row["created_by"])),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _comment_from_row(row: dict[str, Any]) -> ReviewCommentRecord:
        return ReviewCommentRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            review_id=UUID(str(row["review_id"])),
            body=str(row["body"]),
            author_user_id=UUID(str(row["author_user_id"])) if row.get("author_user_id") else None,
            author_name=row.get("author_name"),
            timecode_ms=int(row["timecode_ms"]) if row.get("timecode_ms") is not None else None,
            parent_comment_id=UUID(str(row["parent_comment_id"]))
            if row.get("parent_comment_id")
            else None,
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _link_from_row(row: dict[str, Any]) -> ReviewShareLinkRecord:
        return ReviewShareLinkRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            review_id=UUID(str(row["review_id"])),
            token_digest=str(row["token_digest"]),
            can_comment=bool(row["can_comment"]),
            can_decide=bool(row["can_decide"]),
            expires_at=_datetime(row["expires_at"]),
            revoked_at=_datetime(row["revoked_at"]) if row.get("revoked_at") else None,
            last_accessed_at=_datetime(row["last_accessed_at"])
            if row.get("last_accessed_at")
            else None,
            created_by=UUID(str(row["created_by"])),
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "supabase_request_failed", "The review service could not reach data.")
