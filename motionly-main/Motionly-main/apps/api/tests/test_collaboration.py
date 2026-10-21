"""Task 26 — live-collaboration broadcasts and the member-names directory.

Covers:
  * the collaboration notifier builds the right private-topic broadcast
    (ids/revision only, never content), via a fake HTTP transport;
  * each plan save path emits ``plan_revision_updated`` through the notifier on
    the app state (a recording fake), and the collaboration-manifest save emits
    ``manifest_updated``;
  * the workspace member-names endpoint returns ids + names (no PII) and is
    gated to active members (a non-member is denied by the membership check).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.dependencies import Actor
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.team import InMemoryTeamRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.captions import LanguageHint
from app.schemas.plans import BrollDensity, PlanCreateRequest
from app.schemas.uploads import UploadStatus
from app.services.collaboration import (
    EVENT_MANIFEST_UPDATED,
    EVENT_PLAN_REVISION_UPDATED,
    CollaborationNotifier,
    FakeCollaborationNotifier,
    notifier_from_settings,
)
from app.services.transcription_provider import FixtureTranscriptionProvider


# --------------------------------------------------------------------------
# The notifier itself (real transport).
# --------------------------------------------------------------------------
def _capturing_notifier():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["apikey"] = request.headers.get("apikey")
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = httpx.Response(200).json if False else None
        import json

        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(202, json={"message": "ok"})

    notifier = CollaborationNotifier(
        supabase_url="http://supabase.internal",
        service_role_key="service-key",
    )
    transport = httpx.MockTransport(handler)
    # Patch httpx.post used inside the notifier to route through the transport.
    import app.services.collaboration as collaboration_module

    original_post = collaboration_module.httpx.post

    def patched_post(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    collaboration_module.httpx.post = patched_post
    return notifier, captured, (lambda: setattr(collaboration_module.httpx, "post", original_post))


def test_notifier_broadcasts_plan_revision_on_private_project_topic():
    notifier, captured, restore = _capturing_notifier()
    try:
        project_id = uuid4()
        workspace_id = uuid4()
        plan_id = uuid4()
        notifier.notify_plan_revision_updated(
            project_id=project_id,
            workspace_id=workspace_id,
            plan_id=plan_id,
            revision=7,
        )
    finally:
        restore()

    assert captured["url"] == "http://supabase.internal/realtime/v1/api/broadcast"
    assert captured["apikey"] == "service-key"
    assert captured["auth"] == "Bearer service-key"
    message = captured["json"]["messages"][0]
    assert message["topic"] == f"project:{project_id}"
    assert message["event"] == EVENT_PLAN_REVISION_UPDATED
    assert message["private"] is True
    # Only ids and the revision travel — never plan content.
    assert message["payload"] == {
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "plan_id": str(plan_id),
        "revision": 7,
    }


def test_notifier_broadcasts_manifest_updated():
    notifier, captured, restore = _capturing_notifier()
    try:
        project_id = uuid4()
        workspace_id = uuid4()
        notifier.notify_manifest_updated(
            project_id=project_id, workspace_id=workspace_id, revision=3
        )
    finally:
        restore()
    message = captured["json"]["messages"][0]
    assert message["topic"] == f"project:{project_id}"
    assert message["event"] == EVENT_MANIFEST_UPDATED
    assert message["payload"] == {
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "revision": 3,
    }


def test_notifier_swallows_transport_errors():
    """A broadcast failure must never raise into the save path."""
    notifier = CollaborationNotifier(
        supabase_url="http://supabase.internal",
        service_role_key="service-key",
    )
    import app.services.collaboration as collaboration_module

    original_post = collaboration_module.httpx.post

    def boom(*args, **kwargs):
        raise httpx.ConnectError("realtime down")

    collaboration_module.httpx.post = boom
    try:
        # Does not raise.
        notifier.notify_manifest_updated(
            project_id=uuid4(), workspace_id=uuid4(), revision=1
        )
    finally:
        collaboration_module.httpx.post = original_post


def test_notifier_from_settings_picks_fake_without_supabase():
    fake = notifier_from_settings(
        SimpleNamespace(supabase_url=None, supabase_service_role_key=None)
    )
    assert isinstance(fake, FakeCollaborationNotifier)
    real = notifier_from_settings(
        SimpleNamespace(
            supabase_url="http://supabase.internal",
            supabase_service_role_key="k",
        )
    )
    assert isinstance(real, CollaborationNotifier)


# --------------------------------------------------------------------------
# Save paths emit through the app-state notifier.
# --------------------------------------------------------------------------
@pytest.fixture
def active_plan():
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()

    uploads = InMemoryUploadRepository()
    uploads.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = uploads.create_upload(
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
    uploads.attach_active_video(project_id=project_id, video_id=upload.id)

    captions = InMemoryCaptionRepository(uploads)
    captions.create_transcript(
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
    plans = InMemoryPlanRepository(captions)

    from tests.worker_job_test_helpers import seed_active_plan

    seed_active_plan(
        plan_repository=plans,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=PlanCreateRequest(target_duration_s=10, broll_density=BrollDensity.NONE),
    )
    record = plans.active_plan(project_id=project_id, workspace_id=workspace_id)
    env = SimpleNamespace(
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        plan_repository=plans,
    )
    return env, record


def _request_with_notifier(plan_repository, notifier):
    from app.services.queue import QueueRegistry

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env="test", billing_enabled=False, redis_url=None
                ),
                queue_registry=QueueRegistry(),
                plan_repository=plan_repository,
                operations_repository=None,
                broll_repository=None,
                music_repository=None,
                effects_repository=None,
                collaboration_notifier=notifier,
            ),
        ),
    )


def _actor(env):
    return Actor(user_id=env.user_id, workspace_id=env.workspace_id)


def test_cuts_save_emits_plan_revision_updated(active_plan):
    from app.api.plans import update_plan_cuts
    from app.schemas.plans import EditPlanCut, PlanCutsUpdateRequest

    env, record = active_plan
    notifier = FakeCollaborationNotifier()
    payload = PlanCutsUpdateRequest(
        cuts=[EditPlanCut(start_ms=0, end_ms=5000, reason="keep")],
        expected_revision=record.revision,
    )
    result = asyncio.run(
        update_plan_cuts(
            project_id=env.project_id,
            plan_id=record.id,
            payload=payload,
            actor=_actor(env),
            repository=env.plan_repository,
            request=_request_with_notifier(env.plan_repository, notifier),
        )
    )
    assert len(notifier.messages) == 1
    message = notifier.messages[0]
    assert message["topic"] == f"project:{env.project_id}"
    assert message["event"] == EVENT_PLAN_REVISION_UPDATED
    assert message["payload"]["plan_id"] == str(record.id)
    # The broadcast carries the revision the save just produced, so a peer's
    # tracked revision advances to exactly it (no needless conflict next save).
    assert message["payload"]["revision"] == result.revision
    assert "content" not in message["payload"]
    assert "cuts" not in message["payload"]


def test_manual_elements_save_emits_plan_revision_updated(active_plan):
    from app.api.plans import update_plan_manual_elements
    from app.schemas.plans import ManualElement, PlanManualElementsUpdateRequest

    env, record = active_plan
    notifier = FakeCollaborationNotifier()
    payload = PlanManualElementsUpdateRequest(
        manual_elements=[
            ManualElement(
                id="text-1",
                type="text",
                name="Title",
                zone="canvas",
                start_ms=0,
                end_ms=3000,
                properties={"content": "Hello"},
            )
        ],
        expected_revision=record.revision,
    )
    asyncio.run(
        update_plan_manual_elements(
            project_id=env.project_id,
            plan_id=record.id,
            payload=payload,
            actor=_actor(env),
            repository=env.plan_repository,
            request=_request_with_notifier(env.plan_repository, notifier),
        )
    )
    assert len(notifier.messages) == 1
    assert notifier.messages[0]["event"] == EVENT_PLAN_REVISION_UPDATED


def test_save_without_notifier_does_not_fail(active_plan):
    """A save path with no notifier on app.state still succeeds (no emit)."""
    from app.api.plans import update_plan_cuts
    from app.schemas.plans import EditPlanCut, PlanCutsUpdateRequest
    from app.services.queue import QueueRegistry

    env, record = active_plan
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env="test", billing_enabled=False, redis_url=None
                ),
                queue_registry=QueueRegistry(),
                plan_repository=env.plan_repository,
                operations_repository=None,
                broll_repository=None,
                music_repository=None,
                effects_repository=None,
                # No collaboration_notifier attribute at all.
            ),
        ),
    )
    payload = PlanCutsUpdateRequest(
        cuts=[EditPlanCut(start_ms=0, end_ms=5000, reason="keep")],
        expected_revision=record.revision,
    )
    result = asyncio.run(
        update_plan_cuts(
            project_id=env.project_id,
            plan_id=record.id,
            payload=payload,
            actor=_actor(env),
            repository=env.plan_repository,
            request=request,
        )
    )
    assert result.revision >= record.revision


# --------------------------------------------------------------------------
# Member-names directory.
# --------------------------------------------------------------------------
def _names_request(team_repository):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(team_repository=team_repository))
    )


def test_member_names_endpoint_returns_workspace_members_with_names():
    from app.api.team import list_workspace_member_names

    team = InMemoryTeamRepository()
    workspace_id = uuid4()
    owner_id = uuid4()
    member_id = uuid4()
    team.seed_member(
        workspace_id=workspace_id, user_id=owner_id, role="owner", name="Ada Owner"
    )
    team.seed_member(
        workspace_id=workspace_id, user_id=member_id, role="member", name="Mina Member"
    )
    # A member of a DIFFERENT workspace must not appear.
    other_workspace = uuid4()
    team.seed_member(
        workspace_id=other_workspace, user_id=uuid4(), role="owner", name="Outsider"
    )

    actor = Actor(user_id=owner_id, workspace_id=workspace_id)
    response = asyncio.run(
        list_workspace_member_names(actor=actor, repository=team)
    )
    returned = {str(m.user_id): (m.name, m.role) for m in response.members}
    assert returned == {
        str(owner_id): ("Ada Owner", "owner"),
        str(member_id): ("Mina Member", "member"),
    }


def test_member_names_endpoint_only_returns_active_members():
    from app.api.team import list_workspace_member_names

    team = InMemoryTeamRepository()
    workspace_id = uuid4()
    owner_id = uuid4()
    team.seed_member(workspace_id=workspace_id, user_id=owner_id, role="owner")
    inactive = uuid4()
    team.seed_member(
        workspace_id=workspace_id, user_id=inactive, role="member", status="invited"
    )
    actor = Actor(user_id=owner_id, workspace_id=workspace_id)
    response = asyncio.run(list_workspace_member_names(actor=actor, repository=team))
    ids = {str(m.user_id) for m in response.members}
    assert ids == {str(owner_id)}


def test_member_without_profile_name_gets_non_pii_fallback():
    from app.api.team import list_workspace_member_names

    team = InMemoryTeamRepository()
    workspace_id = uuid4()
    owner_id = uuid4()
    team.seed_member(workspace_id=workspace_id, user_id=owner_id, role="owner")
    actor = Actor(user_id=owner_id, workspace_id=workspace_id)
    response = asyncio.run(list_workspace_member_names(actor=actor, repository=team))
    name = response.members[0].name
    assert name.startswith("Member ")
    # The fallback must not be an email or expose the full id.
    assert "@" not in name
    assert str(owner_id) not in name


def test_membership_check_denies_non_member_names_access(monkeypatch):
    """The names endpoint is gated by get_actor's active-membership check.

    require_workspace_membership raises 403 for a non-member, so the handler is
    never reached by someone outside the workspace.
    """
    from app.api.errors import ApiError
    from app.services import auth as auth_service
    from app.services import membership_cache

    membership_cache.invalidate_membership(uuid4(), uuid4())

    def fake_get(url, **kwargs):
        # PostgREST returns [] when the user is not an active member.
        return httpx.Response(200, json=[])

    monkeypatch.setattr(auth_service.httpx, "get", fake_get)
    with pytest.raises(ApiError) as exc:
        auth_service.require_workspace_membership(
            supabase_url="http://supabase.internal",
            service_role_key="service-key",
            user_id=uuid4(),
            workspace_id=uuid4(),
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "workspace_access_denied"
