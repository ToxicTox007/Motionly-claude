import base64
import hashlib
import hmac
import json
import math
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

import httpx

from app.api.errors import ApiError
from app.core.logging import get_logger, redact_data
from app.repositories.publishing import PublishConnectionRecord, PublishJobRecord
from app.schemas.publishing import PublishConnectionStatus, PublishPlatform, PublishStatus

logger = get_logger(__name__)


@dataclass(frozen=True)
class PublishedPost:
    external_post_id: str | None = None
    external_url: str | None = None
    provider_operation_id: str | None = None
    status: PublishStatus = PublishStatus.PUBLISHED


@dataclass(frozen=True)
class OAuthAccount:
    external_account_id: str
    account_name: str
    access_token: str
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None
    scopes: list[str] | None = None


@dataclass(frozen=True)
class ProviderPublishError:
    provider: PublishPlatform
    error_code: str
    error_category: str
    retryable: bool
    reconnect_required: bool
    user_message: str
    safe_internal_message: str
    retry_after_seconds: int | None = None
    duplicate_publish_risk: bool = False
    provider_operation_id: str | None = None


class PublishOAuthClient:
    def __init__(self, settings) -> None:
        self.settings = settings

    def authorization_url(
        self,
        *,
        platform: PublishPlatform,
        redirect_uri: str,
        state: str,
    ) -> str:
        client_id, _ = self._credentials(platform)
        if platform == PublishPlatform.TIKTOK:
            return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(
                {
                    "client_key": client_id,
                    "response_type": "code",
                    "scope": "user.info.basic,video.publish,video.upload",
                    "redirect_uri": redirect_uri,
                    "state": state,
                }
            )
        if platform == PublishPlatform.INSTAGRAM:
            version = self.settings.meta_graph_api_version
            return f"https://www.facebook.com/{version}/dialog/oauth?" + urlencode(
                {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "state": state,
                    "scope": (
                        "instagram_basic,instagram_content_publish,pages_show_list,"
                        "pages_read_engagement"
                    ),
                }
            )
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )

    def exchange(
        self,
        *,
        platform: PublishPlatform,
        code: str,
        redirect_uri: str,
    ) -> OAuthAccount:
        client_id, client_secret = self._credentials(platform)
        if platform == PublishPlatform.TIKTOK:
            token = self._post_form(
                "https://open.tiktokapis.com/v2/oauth/token/",
                {
                    "client_key": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
                platform=platform,
                phase="token_exchange",
            )
            access_token = str(token.get("access_token") or "")
            identity = self._get_json(
                "https://open.tiktokapis.com/v2/user/info/",
                headers={"authorization": f"Bearer {access_token}"},
                params={"fields": "open_id,display_name"},
                platform=platform,
                phase="account_lookup",
            )
            user = (identity.get("data") or {}).get("user") or {}
            return self._account(
                token=token,
                external_id=str(user.get("open_id") or token.get("open_id") or ""),
                name=str(user.get("display_name") or "TikTok creator"),
            )
        if platform == PublishPlatform.INSTAGRAM:
            version = self.settings.meta_graph_api_version
            token = self._get_json(
                f"https://graph.facebook.com/{version}/oauth/access_token",
                params={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
                platform=platform,
                phase="token_exchange",
            )
            short_lived_token = str(token.get("access_token") or "")
            token = self._get_json(
                f"https://graph.facebook.com/{version}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "fb_exchange_token": short_lived_token,
                },
                platform=platform,
                phase="token_exchange",
            )
            access_token = str(token.get("access_token") or "")
            pages = (
                self._get_json(
                    f"https://graph.facebook.com/{version}/me/accounts",
                    params={
                        "fields": "id,name,access_token,instagram_business_account{id,username}",
                        "access_token": access_token,
                    },
                    platform=platform,
                    phase="account_lookup",
                ).get("data")
                or []
            )
            page = next((item for item in pages if item.get("instagram_business_account")), None)
            if not page:
                raise ApiError(
                    422,
                    "instagram_business_account_missing",
                    "No Instagram professional account is linked to the selected Facebook account.",
                )
            instagram = page["instagram_business_account"]
            token["access_token"] = page.get("access_token") or access_token
            return self._account(
                token=token,
                external_id=str(instagram.get("id") or ""),
                name=str(instagram.get("username") or page.get("name") or "Instagram creator"),
            )
        token = self._post_form(
            "https://oauth2.googleapis.com/token",
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            platform=platform,
            phase="token_exchange",
        )
        access_token = str(token.get("access_token") or "")
        channels = (
            self._get_json(
                "https://www.googleapis.com/youtube/v3/channels",
                headers={"authorization": f"Bearer {access_token}"},
                params={"part": "snippet", "mine": "true"},
                platform=platform,
                phase="account_lookup",
            ).get("items")
            or []
        )
        if not channels:
            raise ApiError(422, "youtube_channel_missing", "No YouTube channel was found.")
        channel = channels[0]
        return self._account(
            token=token,
            external_id=str(channel.get("id") or ""),
            name=str((channel.get("snippet") or {}).get("title") or "YouTube channel"),
        )

    def refresh(self, connection: PublishConnectionRecord) -> OAuthAccount:
        client_id, client_secret = self._credentials(connection.platform)
        if connection.platform == PublishPlatform.YOUTUBE:
            if not connection.refresh_token:
                raise ApiError(
                    401,
                    "publish_reauthorization_required",
                    "YouTube authorization expired. Reconnect the account.",
                )
            token = self._post_form(
                "https://oauth2.googleapis.com/token",
                {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": connection.refresh_token,
                    "grant_type": "refresh_token",
                },
                platform=connection.platform,
                phase="refresh",
            )
        elif connection.platform == PublishPlatform.TIKTOK:
            if not connection.refresh_token:
                raise ApiError(
                    401,
                    "publish_reauthorization_required",
                    "TikTok authorization expired. Reconnect the account.",
                )
            token = self._post_form(
                "https://open.tiktokapis.com/v2/oauth/token/",
                {
                    "client_key": client_id,
                    "client_secret": client_secret,
                    "refresh_token": connection.refresh_token,
                    "grant_type": "refresh_token",
                },
                platform=connection.platform,
                phase="refresh",
            )
        else:
            # Meta long-lived user/page tokens are renewed using the current token.
            token = self._get_json(
                f"https://graph.facebook.com/{self.settings.meta_graph_api_version}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "fb_exchange_token": connection.access_token,
                },
                platform=connection.platform,
                phase="refresh",
            )
        if not token.get("refresh_token") and connection.refresh_token:
            token["refresh_token"] = connection.refresh_token
        return self._account(
            token=token,
            external_id=connection.external_account_id,
            name=connection.account_name,
        )

    def _credentials(self, platform: PublishPlatform) -> tuple[str, str]:
        values = {
            PublishPlatform.TIKTOK: (
                self.settings.tiktok_client_key,
                self.settings.tiktok_client_secret,
            ),
            PublishPlatform.INSTAGRAM: (
                self.settings.meta_app_id,
                self.settings.meta_app_secret,
            ),
            PublishPlatform.YOUTUBE: (
                self.settings.google_client_id,
                self.settings.google_client_secret,
            ),
        }[platform]
        if not all(values):
            raise ApiError(
                503,
                "publish_oauth_not_configured",
                f"{platform.value.title()} OAuth is not configured.",
            )
        return str(values[0]), str(values[1])

    @staticmethod
    def _account(*, token: dict[str, object], external_id: str, name: str) -> OAuthAccount:
        access_token = str(token.get("access_token") or "")
        if not access_token or not external_id:
            raise ApiError(
                502, "publish_oauth_failed", "The provider returned incomplete account data."
            )
        expires_in = int(token.get("expires_in") or 0)
        return OAuthAccount(
            external_account_id=external_id,
            account_name=name,
            access_token=access_token,
            refresh_token=str(token["refresh_token"]) if token.get("refresh_token") else None,
            token_expires_at=datetime.now(UTC) + timedelta(seconds=expires_in)
            if expires_in
            else None,
            refresh_token_expires_at=(
                datetime.now(UTC) + timedelta(seconds=int(token.get("refresh_expires_in") or 0))
                if int(token.get("refresh_expires_in") or 0)
                else None
            ),
            scopes=_parse_scopes(token.get("scope")),
        )

    @staticmethod
    def _post_form(
        url: str, data: dict[str, str], *, platform: PublishPlatform, phase: str
    ) -> dict[str, object]:
        response = httpx.post(url, data=data, timeout=30)
        return PublishOAuthClient._oauth_response(response, platform=platform, phase=phase)

    @staticmethod
    def _get_json(
        url: str,
        *,
        platform: PublishPlatform,
        phase: str,
        **kwargs: object,
    ) -> dict[str, object]:
        response = httpx.get(url, timeout=30, **kwargs)
        return PublishOAuthClient._oauth_response(response, platform=platform, phase=phase)

    @staticmethod
    def _oauth_response(
        response: httpx.Response, *, platform: PublishPlatform, phase: str
    ) -> dict[str, object]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(
                502, "publish_oauth_failed", "The provider returned invalid OAuth data."
            ) from exc
        if response.status_code >= 400 or payload.get("error"):
            error = _map_oauth_error(
                platform=platform,
                phase=phase,
                status_code=response.status_code,
                payload=payload,
            )
            raise _provider_error_to_api_error(error)
        return payload


def create_oauth_state(
    *, secret: str, workspace_id: UUID, platform: PublishPlatform, return_url: str
) -> str:
    payload = json.dumps(
        {
            "workspace_id": str(workspace_id),
            "platform": platform.value,
            "return_url": return_url,
            "expires_at": int(time.time()) + 600,
            "nonce": uuid.uuid4().hex,
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    encoded_payload = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{encoded_payload}.{encoded_signature}"


def read_oauth_state(*, secret: str, state: str) -> dict[str, str]:
    try:
        payload_value, signature_value = state.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_value + "=" * (-len(payload_value) % 4))
        signature = base64.urlsafe_b64decode(signature_value + "=" * (-len(signature_value) % 4))
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        values = json.loads(payload)
        if int(values["expires_at"]) < int(time.time()):
            raise ValueError("expired")
        return values
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ApiError(
            400, "publish_oauth_state_invalid", "Account connection expired or is invalid."
        ) from exc


class SocialPublishingClient:
    """Provider API adapter. Tokens are only read server-side and never returned by the API."""

    def __init__(self, *, meta_graph_api_version: str = "v22.0") -> None:
        self.meta_graph_api_version = meta_graph_api_version

    def publish(
        self,
        *,
        connection: PublishConnectionRecord,
        job: PublishJobRecord,
        video_url: str,
    ) -> PublishedPost:
        try:
            if connection.platform == PublishPlatform.TIKTOK:
                return self._tiktok(connection=connection, job=job, video_url=video_url)
            if connection.platform == PublishPlatform.INSTAGRAM:
                return self._instagram(connection=connection, job=job, video_url=video_url)
            return self._youtube(connection=connection, job=job, video_url=video_url)
        except ApiError:
            raise
        except httpx.TimeoutException as exc:
            raise _provider_error_to_api_error(
                ProviderPublishError(
                    provider=connection.platform,
                    error_code="provider_timeout",
                    error_category="network_error",
                    retryable=True,
                    reconnect_required=False,
                    user_message=(
                        f"{connection.platform.value.title()} timed out while publishing. "
                        "We'll retry automatically."
                    ),
                    safe_internal_message="provider request timed out",
                )
            ) from exc
        except httpx.HTTPError as exc:
            raise _provider_error_to_api_error(
                ProviderPublishError(
                    provider=connection.platform,
                    error_code="provider_unavailable",
                    error_category="network_error",
                    retryable=True,
                    reconnect_required=False,
                    user_message=(
                        f"{connection.platform.value.title()} could not be reached. "
                        "We'll retry automatically."
                    ),
                    safe_internal_message="provider network error",
                )
            ) from exc

    def _tiktok(
        self, *, connection: PublishConnectionRecord, job: PublishJobRecord, video_url: str
    ) -> PublishedPost:
        privacy = {
            "public": "PUBLIC_TO_EVERYONE",
            "private": "SELF_ONLY",
            "unlisted": "MUTUAL_FOLLOW_FRIENDS",
        }[job.privacy]
        response = httpx.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "authorization": f"Bearer {connection.access_token}",
                "content-type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": {
                    "title": (job.caption or job.title)[:2200],
                    "privacy_level": privacy,
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                },
                "source_info": {"source": "PULL_FROM_URL", "video_url": video_url},
            },
            timeout=30,
        )
        payload = self._json_response(response, platform=PublishPlatform.TIKTOK, phase="upload")
        post_id = str((payload.get("data") or {}).get("publish_id") or "")
        if not post_id:
            raise ApiError(502, "social_publish_failed", "TikTok did not return a publish id.")
        for _ in range(60):
            status_response = httpx.post(
                "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
                headers={
                    "authorization": f"Bearer {connection.access_token}",
                    "content-type": "application/json; charset=UTF-8",
                },
                json={"publish_id": post_id},
                timeout=30,
            )
            status_payload = self._json_response(
                status_response, platform=PublishPlatform.TIKTOK, phase="processing"
            )
            status = str((status_payload.get("data") or {}).get("status") or "")
            if status == "PUBLISH_COMPLETE":
                break
            if status == "SEND_TO_USER_INBOX":
                return PublishedPost(
                    external_post_id=post_id,
                    provider_operation_id=post_id,
                    status=PublishStatus.PROCESSING,
                )
            if status == "FAILED":
                raise _provider_error_to_api_error(
                    ProviderPublishError(
                        provider=PublishPlatform.TIKTOK,
                        error_code="processing_failed",
                        error_category="processing_failed",
                        retryable=False,
                        reconnect_required=False,
                        user_message="TikTok could not finish processing the video.",
                        safe_internal_message="tiktok processing failed",
                        duplicate_publish_risk=True,
                        provider_operation_id=post_id,
                    )
                )
            time.sleep(2)
        else:
            raise _provider_error_to_api_error(
                ProviderPublishError(
                    provider=PublishPlatform.TIKTOK,
                    error_code="processing_failed",
                    error_category="processing_failed",
                    retryable=False,
                    reconnect_required=False,
                    user_message="TikTok is still processing the video. Check back shortly.",
                    safe_internal_message="tiktok processing timed out",
                    duplicate_publish_risk=True,
                    provider_operation_id=post_id,
                )
            )
        return PublishedPost(external_post_id=post_id, provider_operation_id=post_id)

    def _instagram(
        self, *, connection: PublishConnectionRecord, job: PublishJobRecord, video_url: str
    ) -> PublishedPost:
        create = httpx.post(
            f"https://graph.facebook.com/{self.meta_graph_api_version}/{connection.external_account_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": job.caption or job.title,
                "access_token": connection.access_token,
            },
            timeout=30,
        )
        creation_id = str(
            self._json_response(
                create,
                platform=PublishPlatform.INSTAGRAM,
                phase="upload",
            ).get("id")
            or ""
        )
        if not creation_id:
            raise ApiError(
                502, "social_publish_failed", "Instagram did not create a media container."
            )
        for _ in range(30):
            status = httpx.get(
                f"https://graph.facebook.com/{self.meta_graph_api_version}/{creation_id}",
                params={"fields": "status_code", "access_token": connection.access_token},
                timeout=30,
            )
            status_code = str(
                self._json_response(
                    status, platform=PublishPlatform.INSTAGRAM, phase="processing"
                ).get("status_code")
                or ""
            )
            if status_code == "FINISHED":
                break
            if status_code in {"ERROR", "EXPIRED"}:
                raise _provider_error_to_api_error(
                    ProviderPublishError(
                        provider=PublishPlatform.INSTAGRAM,
                        error_code="processing_failed",
                        error_category="processing_failed",
                        retryable=False,
                        reconnect_required=False,
                        user_message="Instagram could not process the reel.",
                        safe_internal_message="instagram media container processing failed",
                        duplicate_publish_risk=True,
                        provider_operation_id=creation_id,
                    )
                )
            time.sleep(2)
        else:
            raise _provider_error_to_api_error(
                ProviderPublishError(
                    provider=PublishPlatform.INSTAGRAM,
                    error_code="processing_failed",
                    error_category="processing_failed",
                    retryable=False,
                    reconnect_required=False,
                    user_message="Instagram is still processing the reel. Check back shortly.",
                    safe_internal_message="instagram processing timed out",
                    duplicate_publish_risk=True,
                    provider_operation_id=creation_id,
                )
            )
        publish = httpx.post(
            f"https://graph.facebook.com/{self.meta_graph_api_version}/{connection.external_account_id}/media_publish",
            data={"creation_id": creation_id, "access_token": connection.access_token},
            timeout=30,
        )
        post_id = str(
            self._json_response(publish, platform=PublishPlatform.INSTAGRAM, phase="publish").get(
                "id"
            )
            or ""
        )
        if not post_id:
            raise ApiError(502, "social_publish_failed", "Instagram did not return a post id.")
        permalink_response = httpx.get(
            f"https://graph.facebook.com/{self.meta_graph_api_version}/{post_id}",
            params={"fields": "permalink", "access_token": connection.access_token},
            timeout=30,
        )
        permalink = None
        if permalink_response.status_code < 400:
            permalink = str(permalink_response.json().get("permalink") or "") or None
        return PublishedPost(
            external_post_id=post_id,
            external_url=permalink,
            provider_operation_id=creation_id,
        )

    def _youtube(
        self, *, connection: PublishConnectionRecord, job: PublishJobRecord, video_url: str
    ) -> PublishedPost:
        privacy = "unlisted" if job.privacy == "unlisted" else job.privacy
        metadata = {
            "snippet": {"title": job.title, "description": job.caption},
            "status": {"privacyStatus": privacy},
        }
        initiation = httpx.post(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
            headers={
                "authorization": f"Bearer {connection.access_token}",
                "content-type": "application/json; charset=UTF-8",
            },
            json=metadata,
            timeout=30,
        )
        if initiation.status_code >= 400:
            self._json_response(initiation, platform=PublishPlatform.YOUTUBE, phase="upload")
        upload_url = initiation.headers.get("location")
        if not upload_url:
            raise ApiError(502, "social_publish_failed", "YouTube did not start the upload.")
        try:
            with httpx.stream("GET", video_url, timeout=120) as media:
                media.raise_for_status()
                headers = {"content-type": media.headers.get("content-type", "video/mp4")}
                if media.headers.get("content-length"):
                    headers["content-length"] = media.headers["content-length"]
                response = httpx.put(
                    upload_url,
                    headers=headers,
                    content=media.iter_bytes(chunk_size=1024 * 1024),
                    timeout=300,
                )
        except httpx.HTTPError as exc:
            raise _provider_error_to_api_error(
                ProviderPublishError(
                    provider=PublishPlatform.YOUTUBE,
                    error_code="duplicate_publish_risk",
                    error_category="upload_failed",
                    retryable=False,
                    reconnect_required=False,
                    user_message=(
                        "YouTube upload state is uncertain. Review the post before retrying."
                    ),
                    safe_internal_message="youtube upload session failed after initiation",
                    duplicate_publish_risk=True,
                )
            ) from exc
        post_id = str(
            self._json_response(response, platform=PublishPlatform.YOUTUBE, phase="upload").get(
                "id"
            )
            or ""
        )
        if not post_id:
            raise ApiError(502, "social_publish_failed", "YouTube did not return a video id.")
        return PublishedPost(external_post_id=post_id, external_url=f"https://youtu.be/{post_id}")

    def _json_response(
        self,
        response: httpx.Response,
        *,
        platform: PublishPlatform | None = None,
        phase: str = "upload",
    ) -> dict[str, object]:
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = {}
            provider = platform or PublishPlatform.YOUTUBE
            error = _map_publish_error(
                provider=provider,
                phase=phase,
                status_code=response.status_code,
                headers=response.headers,
                payload=detail,
            )
            raise _provider_error_to_api_error(error)
        try:
            return response.json()
        except ValueError as exc:
            provider = platform or PublishPlatform.YOUTUBE
            raise _provider_error_to_api_error(
                ProviderPublishError(
                    provider=provider,
                    error_code="malformed_response",
                    error_category="malformed_response",
                    retryable=False,
                    reconnect_required=False,
                    user_message=f"{provider.value.title()} returned an invalid response.",
                    safe_internal_message="provider returned invalid json payload",
                )
            ) from exc


def execute_publish_job(
    *,
    publishing_repository,
    render_repository,
    storage_signer,
    client: SocialPublishingClient,
    oauth_client: PublishOAuthClient | None = None,
    settings=None,
    job_id,
    workspace_id,
) -> PublishJobRecord:
    job = publishing_repository.get_job(job_id=job_id, workspace_id=workspace_id)
    if job.status in {
        PublishStatus.PUBLISHED,
        PublishStatus.PROCESSING,
        PublishStatus.DUPLICATE_RISK_NEEDS_REVIEW,
    }:
        return job
    connection = publishing_repository.get_connection(
        connection_id=job.connection_id, workspace_id=workspace_id
    )
    if connection.connection_status == PublishConnectionStatus.RECONNECT_REQUIRED:
        return publishing_repository.update_job(
            job=job,
            status=PublishStatus.RECONNECT_REQUIRED,
            error_code="publish_reauthorization_required",
            error_category="reconnect_required",
            error_message="Reconnect the publishing account before trying again.",
            user_message="Reconnect the publishing account before trying again.",
            reconnect_required=True,
            retryable=False,
        )
    try:
        connection = _refresh_connection_if_needed(
            publishing_repository=publishing_repository,
            oauth_client=oauth_client,
            connection=connection,
            job=job,
            settings=settings,
        )
    except ApiError:
        return publishing_repository.get_job(job_id=job.id, workspace_id=workspace_id)
    export = render_repository.get_export(export_id=job.export_id, workspace_id=workspace_id)
    publishing_repository.update_job(
        job=job,
        status=PublishStatus.UPLOADING,
        error_message=None,
        error_code=None,
        error_category=None,
        user_message=None,
        reconnect_required=False,
        retryable=None,
        retry_after_seconds=None,
        duplicate_publish_risk=False,
    )
    download = storage_signer.create_signed_download(
        bucket=export.storage_bucket, path=export.storage_path, expires_in_seconds=3600
    )
    try:
        try:
            result = client.publish(connection=connection, job=job, video_url=download.url)
        except ApiError as exc:
            if (
                exc.code != "publish_token_expired"
                or oauth_client is None
                or job.retry_count >= 1
            ):
                raise
            connection = _refresh_connection(
                publishing_repository=publishing_repository,
                oauth_client=oauth_client,
                connection=connection,
                job=job,
                settings=settings,
                reason="provider_auth_challenge",
            )
            publishing_repository.update_job(job=job, retry_count=job.retry_count + 1)
            result = client.publish(connection=connection, job=job, video_url=download.url)
    except ApiError as exc:
        details = dict(exc.details or {})
        error = ProviderPublishError(
            provider=connection.platform,
            error_code=str(details.get("error_code") or exc.code),
            error_category=str(details.get("error_category") or "provider_error"),
            retryable=bool(details.get("retryable") or False),
            reconnect_required=bool(details.get("reconnect_required") or False),
            user_message=exc.message,
            safe_internal_message=str(details.get("safe_internal_message") or exc.code),
            retry_after_seconds=(
                int(details["retry_after_seconds"])
                if details.get("retry_after_seconds") is not None
                else None
            ),
            duplicate_publish_risk=bool(details.get("duplicate_publish_risk") or False),
            provider_operation_id=str(details["provider_operation_id"])
            if details.get("provider_operation_id")
            else None,
        )
        if error.reconnect_required:
            updated = _mark_reconnect_required(
                publishing_repository=publishing_repository,
                connection=connection,
                job=job,
                error=error,
            )
            return updated
        if error.duplicate_publish_risk:
            return publishing_repository.update_job(
                job=job,
                status=PublishStatus.DUPLICATE_RISK_NEEDS_REVIEW,
                provider_operation_id=error.provider_operation_id,
                error_code=error.error_code,
                error_category=error.error_category,
                error_message=error.user_message,
                user_message=error.user_message,
                retryable=False,
                reconnect_required=False,
                duplicate_publish_risk=True,
            )
        if error.retryable and job.retry_count < _publish_retry_max_attempts(settings) - 1:
            backoff_seconds = _retry_backoff_seconds(
                settings=settings,
                retry_count=job.retry_count + 1,
                retry_after_seconds=error.retry_after_seconds,
            )
            publishing_repository.update_job(
                job=job,
                status=PublishStatus.RETRY_SCHEDULED,
                error_code=error.error_code,
                error_category=error.error_category,
                error_message=error.user_message,
                user_message=error.user_message,
                retryable=True,
                reconnect_required=False,
                retry_after_seconds=backoff_seconds,
                retry_count=job.retry_count + 1,
                duplicate_publish_risk=False,
                provider_operation_id=error.provider_operation_id,
            )
            _log_publish_event(
                "publish_retry_scheduled",
                platform=connection.platform,
                workspace_id=workspace_id,
                project_id=job.project_id,
                publish_job_id=job.id,
                retry_after_seconds=backoff_seconds,
                error_code=error.error_code,
                error_category=error.error_category,
            )
            raise
        return publishing_repository.update_job(
            job=job,
            status=PublishStatus.FAILED,
            error_code=error.error_code,
            error_category=error.error_category,
            error_message=error.user_message,
            user_message=error.user_message,
            retryable=error.retryable,
            reconnect_required=False,
            retry_after_seconds=error.retry_after_seconds,
            duplicate_publish_risk=False,
            provider_operation_id=error.provider_operation_id,
        )
    except Exception:
        _log_publish_event(
            "publish_failed_unexpectedly",
            platform=connection.platform,
            workspace_id=workspace_id,
            project_id=job.project_id,
            publish_job_id=job.id,
        )
        return publishing_repository.update_job(
            job=job,
            status=PublishStatus.FAILED,
            error_code="upload_failed",
            error_category="upload_failed",
            error_message="Publishing failed unexpectedly.",
            user_message="Publishing failed unexpectedly.",
            retryable=False,
            reconnect_required=False,
        )
    final_status = result.status
    return publishing_repository.update_job(
        job=job,
        status=final_status,
        external_post_id=result.external_post_id,
        external_url=result.external_url,
        provider_operation_id=result.provider_operation_id,
        error_message=None,
        user_message=(
            "The platform is still processing this post."
            if final_status == PublishStatus.PROCESSING
            else None
        ),
        error_code=None,
        error_category=None,
        retryable=False,
        reconnect_required=False,
        retry_after_seconds=None,
        duplicate_publish_risk=False,
    )


def refresh_connections_due_for_publish(*, publishing_repository, oauth_client, settings) -> int:
    refreshed = 0
    for connection in publishing_repository.list_all_connections():
        if connection.connection_status == PublishConnectionStatus.RECONNECT_REQUIRED:
            continue
        if not _connection_needs_refresh(connection=connection, settings=settings):
            continue
        try:
            _refresh_connection(
                publishing_repository=publishing_repository,
                oauth_client=oauth_client,
                connection=connection,
                settings=settings,
                reason="scheduled_refresh",
            )
            refreshed += 1
        except ApiError:
            continue
    return refreshed


def _refresh_connection_if_needed(
    *,
    publishing_repository,
    oauth_client: PublishOAuthClient | None,
    connection: PublishConnectionRecord,
    job: PublishJobRecord,
    settings=None,
) -> PublishConnectionRecord:
    if oauth_client is None or not _connection_needs_refresh(
        connection=connection,
        settings=settings,
    ):
        return connection
    return _refresh_connection(
        publishing_repository=publishing_repository,
        oauth_client=oauth_client,
        connection=connection,
        job=job,
        settings=settings,
        reason="pre_publish_refresh",
    )


def _refresh_connection(
    *,
    publishing_repository,
    oauth_client: PublishOAuthClient,
    connection: PublishConnectionRecord,
    settings=None,
    job: PublishJobRecord | None = None,
    reason: str,
) -> PublishConnectionRecord:
    if job is not None:
        publishing_repository.update_job(job=job, status=PublishStatus.REFRESHING_TOKEN)
    try:
        refreshed = oauth_client.refresh(connection)
    except ApiError as exc:
        error = ProviderPublishError(
            provider=connection.platform,
            error_code=str(exc.details.get("error_code") or exc.code),
            error_category=str(exc.details.get("error_category") or "reconnect_required"),
            retryable=bool(exc.details.get("retryable") or False),
            reconnect_required=bool(exc.details.get("reconnect_required", True)),
            user_message=exc.message,
            safe_internal_message=str(exc.details.get("safe_internal_message") or exc.code),
        )
        publishing_repository.update_connection(
            connection=connection,
            connection_status=PublishConnectionStatus.RECONNECT_REQUIRED,
            last_refresh_at=datetime.now(UTC),
            last_refresh_failure_reason=error.error_code,
        )
        _log_publish_event(
            "publish_token_refresh_failed",
            platform=connection.platform,
            publish_job_id=job.id if job else None,
            workspace_id=connection.workspace_id,
            error_code=error.error_code,
            error_category=error.error_category,
            reason=reason,
        )
        if job is not None:
            _mark_reconnect_required(
                publishing_repository=publishing_repository,
                connection=connection,
                job=job,
                error=error,
            )
        raise
    publishing_repository.update_connection_tokens(
        connection=connection,
        access_token=refreshed.access_token,
        refresh_token=refreshed.refresh_token,
        token_expires_at=refreshed.token_expires_at,
        refresh_token_expires_at=refreshed.refresh_token_expires_at,
        scopes=refreshed.scopes,
        connection_status=PublishConnectionStatus.ACTIVE,
        last_refresh_at=datetime.now(UTC),
        last_refresh_failure_reason=None,
    )
    _log_publish_event(
        "publish_token_refreshed",
        platform=connection.platform,
        publish_job_id=job.id if job else None,
        workspace_id=connection.workspace_id,
        reason=reason,
    )
    return publishing_repository.get_connection(
        connection_id=connection.id,
        workspace_id=connection.workspace_id,
    )


def _mark_reconnect_required(
    *,
    publishing_repository,
    connection,
    job,
    error: ProviderPublishError,
):
    publishing_repository.update_connection(
        connection=connection,
        connection_status=PublishConnectionStatus.RECONNECT_REQUIRED,
        last_refresh_failure_reason=error.error_code,
    )
    return publishing_repository.update_job(
        job=job,
        status=PublishStatus.RECONNECT_REQUIRED,
        error_code=error.error_code,
        error_category=error.error_category,
        error_message=error.user_message,
        user_message=error.user_message,
        retryable=False,
        reconnect_required=True,
        duplicate_publish_risk=False,
        provider_operation_id=error.provider_operation_id,
    )


def _connection_needs_refresh(*, connection: PublishConnectionRecord, settings=None) -> bool:
    if connection.token_expires_at is None:
        return False
    return connection.token_expires_at <= datetime.now(UTC) + timedelta(
        seconds=_publish_refresh_buffer_seconds(settings)
    )


def _publish_refresh_buffer_seconds(settings) -> int:
    return max(0, int(getattr(settings, "publish_token_refresh_buffer_seconds", 300)))


def _publish_retry_max_attempts(settings) -> int:
    return max(1, int(getattr(settings, "publish_retry_max_attempts", 3)))


def _retry_backoff_seconds(*, settings, retry_count: int, retry_after_seconds: int | None) -> int:
    configured = max(1, int(getattr(settings, "publish_retry_backoff_seconds", 60)))
    cap = max(configured, int(getattr(settings, "publish_retry_backoff_cap_seconds", 900)))
    if retry_after_seconds is not None:
        return min(cap, max(1, retry_after_seconds))
    return min(cap, int(configured * math.pow(2, max(0, retry_count - 1))))


def _parse_scopes(raw_scope: object) -> list[str]:
    if raw_scope is None:
        return []
    if isinstance(raw_scope, list):
        return [str(item) for item in raw_scope if str(item).strip()]
    scope_text = str(raw_scope).replace(",", " ")
    return [segment for segment in scope_text.split() if segment]


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(1, int(value))
    except ValueError:
        return None


def _payload_text(payload: dict[str, object]) -> str:
    error_payload = payload.get("error") or {}
    parts = [
        str(payload.get("message") or ""),
        str(payload.get("error_description") or ""),
        str(payload.get("error") or ""),
        str(error_payload.get("message") if isinstance(error_payload, dict) else ""),
    ]
    return " ".join(part for part in parts if part).lower()


def _map_oauth_error(
    *,
    platform: PublishPlatform,
    phase: str,
    status_code: int,
    payload: dict[str, object],
) -> ProviderPublishError:
    text = _payload_text(payload)
    if any(token in text for token in ("invalid_grant", "invalid refresh", "revoked", "expired")):
        return ProviderPublishError(
            provider=platform,
            error_code="revoked_token",
            error_category="reconnect_required",
            retryable=False,
            reconnect_required=True,
            user_message=f"{platform.value.title()} authorization expired. Reconnect the account.",
            safe_internal_message=f"{phase} invalid or revoked grant",
        )
    if "scope" in text:
        return ProviderPublishError(
            provider=platform,
            error_code="insufficient_scope",
            error_category="reconnect_required",
            retryable=False,
            reconnect_required=True,
            user_message=f"{platform.value.title()} permissions changed. Reconnect the account.",
            safe_internal_message=f"{phase} insufficient scope",
        )
    return ProviderPublishError(
        provider=platform,
        error_code="publish_oauth_failed",
        error_category="oauth_failed",
        retryable=status_code >= 500,
        reconnect_required=False,
        user_message=f"{platform.value.title()} rejected the account connection.",
        safe_internal_message=f"{phase} oauth exchange failed",
    )


def _map_publish_error(
    *,
    provider: PublishPlatform,
    phase: str,
    status_code: int,
    headers,
    payload: dict[str, object],
) -> ProviderPublishError:
    text = _payload_text(payload)
    retry_after = _retry_after_seconds(headers.get("retry-after"))
    if status_code == 429:
        return ProviderPublishError(
            provider=provider,
            error_code="provider_rate_limited",
            error_category="provider_rate_limited",
            retryable=True,
            reconnect_required=False,
            user_message=(
                f"{provider.value.title()} rate limited this publish. "
                "We'll retry automatically."
            ),
            safe_internal_message=f"{phase} provider rate limited request",
            retry_after_seconds=retry_after,
        )
    if status_code in {401, 403}:
        if "scope" in text:
            return ProviderPublishError(
                provider=provider,
                error_code="insufficient_scope",
                error_category="reconnect_required",
                retryable=False,
                reconnect_required=True,
                user_message=f"{provider.value.title()} permissions need to be reconnected.",
                safe_internal_message=f"{phase} insufficient scope",
            )
        if "permission" in text or "not authorized" in text:
            return ProviderPublishError(
                provider=provider,
                error_code="account_permission_issue",
                error_category="account_permission_issue",
                retryable=False,
                reconnect_required=False,
                user_message=f"{provider.value.title()} denied access to this account.",
                safe_internal_message=f"{phase} account permission denied",
            )
        return ProviderPublishError(
            provider=provider,
            error_code="publish_token_expired",
            error_category="expired_token",
            retryable=False,
            reconnect_required=False,
            user_message="Publishing authorization expired. Refreshing the connection is required.",
            safe_internal_message=f"{phase} provider auth challenge",
        )
    if any(token in text for token in ("scope", "permission", "role")):
        permission_issue = "permission" in text or "role" in text
        return ProviderPublishError(
            provider=provider,
            error_code=(
                "account_permission_issue" if permission_issue else "insufficient_scope"
            ),
            error_category=(
                "account_permission_issue" if permission_issue else "reconnect_required"
            ),
            retryable=False,
            reconnect_required="scope" in text,
            user_message=(
                f"{provider.value.title()} permissions need to be reconnected."
                if "scope" in text
                else f"{provider.value.title()} denied access to this account."
            ),
            safe_internal_message=f"{phase} provider permissions failure",
        )
    if any(token in text for token in ("format", "codec", "unsupported", "mime")):
        return ProviderPublishError(
            provider=provider,
            error_code="unsupported_video_format",
            error_category="unsupported_video_format",
            retryable=False,
            reconnect_required=False,
            user_message=f"{provider.value.title()} rejected the current video format.",
            safe_internal_message=f"{phase} unsupported video format",
        )
    if status_code >= 500:
        return ProviderPublishError(
            provider=provider,
            error_code="upload_failed" if phase == "upload" else "processing_failed",
            error_category="upload_failed" if phase == "upload" else "processing_failed",
            retryable=True,
            reconnect_required=False,
            user_message=(
                f"{provider.value.title()} failed while {phase}. "
                "We'll retry automatically."
            ),
            safe_internal_message=f"{phase} provider server failure",
            retry_after_seconds=retry_after,
        )
    return ProviderPublishError(
        provider=provider,
        error_code="upload_failed" if phase == "upload" else "processing_failed",
        error_category="upload_failed" if phase == "upload" else "processing_failed",
        retryable=False,
        reconnect_required=False,
        user_message=f"{provider.value.title()} rejected the publish request.",
        safe_internal_message=f"{phase} provider rejected request",
    )


def _provider_error_to_api_error(error: ProviderPublishError) -> ApiError:
    status_code = (
        429
        if error.error_code == "provider_rate_limited"
        else 401
        if error.reconnect_required
        else 502
    )
    return ApiError(
        status_code,
        error.error_code,
        error.user_message,
        details={
            "provider": error.provider.value,
            "error_code": error.error_code,
            "error_category": error.error_category,
            "retryable": error.retryable,
            "reconnect_required": error.reconnect_required,
            "retry_after_seconds": error.retry_after_seconds,
            "duplicate_publish_risk": error.duplicate_publish_risk,
            "provider_operation_id": error.provider_operation_id,
            "safe_internal_message": error.safe_internal_message,
        },
    )


def _log_publish_event(event: str, **kwargs: object) -> None:
    logger.warning(event, extra=redact_data(kwargs))
