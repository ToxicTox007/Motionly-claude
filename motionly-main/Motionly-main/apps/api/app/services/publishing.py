import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

import httpx

from app.api.errors import ApiError
from app.repositories.publishing import PublishConnectionRecord, PublishJobRecord
from app.schemas.publishing import PublishPlatform, PublishStatus


@dataclass(frozen=True)
class PublishedPost:
    external_post_id: str
    external_url: str | None = None


@dataclass(frozen=True)
class OAuthAccount:
    external_account_id: str
    account_name: str
    access_token: str
    refresh_token: str | None = None
    token_expires_at: datetime | None = None


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
            )
            access_token = str(token.get("access_token") or "")
            identity = self._get_json(
                "https://open.tiktokapis.com/v2/user/info/",
                headers={"authorization": f"Bearer {access_token}"},
                params={"fields": "open_id,display_name"},
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
            )
            access_token = str(token.get("access_token") or "")
            pages = (
                self._get_json(
                    f"https://graph.facebook.com/{version}/me/accounts",
                    params={
                        "fields": "id,name,access_token,instagram_business_account{id,username}",
                        "access_token": access_token,
                    },
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
        )
        access_token = str(token.get("access_token") or "")
        channels = (
            self._get_json(
                "https://www.googleapis.com/youtube/v3/channels",
                headers={"authorization": f"Bearer {access_token}"},
                params={"part": "snippet", "mine": "true"},
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
        )

    @staticmethod
    def _post_form(url: str, data: dict[str, str]) -> dict[str, object]:
        response = httpx.post(url, data=data, timeout=30)
        return PublishOAuthClient._oauth_response(response)

    @staticmethod
    def _get_json(url: str, **kwargs: object) -> dict[str, object]:
        response = httpx.get(url, timeout=30, **kwargs)
        return PublishOAuthClient._oauth_response(response)

    @staticmethod
    def _oauth_response(response: httpx.Response) -> dict[str, object]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(
                502, "publish_oauth_failed", "The provider returned invalid OAuth data."
            ) from exc
        if response.status_code >= 400 or payload.get("error"):
            raise ApiError(502, "publish_oauth_failed", "The provider rejected account connection.")
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
        except httpx.HTTPError as exc:
            raise ApiError(
                502, "social_publish_failed", "The social platform could not be reached."
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
        payload = self._json_response(response)
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
            status_payload = self._json_response(status_response)
            status = str((status_payload.get("data") or {}).get("status") or "")
            if status == "PUBLISH_COMPLETE":
                break
            if status in {"FAILED", "SEND_TO_USER_INBOX"}:
                if status == "SEND_TO_USER_INBOX":
                    return PublishedPost(external_post_id=post_id)
                raise ApiError(502, "social_publish_failed", "TikTok could not publish the video.")
            time.sleep(2)
        else:
            raise ApiError(
                504,
                "social_publish_timeout",
                "TikTok is still processing the video. Try again shortly.",
            )
        return PublishedPost(external_post_id=post_id)

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
        creation_id = str(self._json_response(create).get("id") or "")
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
            status_code = str(self._json_response(status).get("status_code") or "")
            if status_code == "FINISHED":
                break
            if status_code in {"ERROR", "EXPIRED"}:
                raise ApiError(
                    502, "social_publish_failed", "Instagram could not process the reel."
                )
            time.sleep(2)
        else:
            raise ApiError(
                504,
                "social_publish_timeout",
                "Instagram is still processing the reel. Try publishing again shortly.",
            )
        publish = httpx.post(
            f"https://graph.facebook.com/{self.meta_graph_api_version}/{connection.external_account_id}/media_publish",
            data={"creation_id": creation_id, "access_token": connection.access_token},
            timeout=30,
        )
        post_id = str(self._json_response(publish).get("id") or "")
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
        return PublishedPost(external_post_id=post_id, external_url=permalink)

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
            self._json_response(initiation)
        upload_url = initiation.headers.get("location")
        if not upload_url:
            raise ApiError(502, "social_publish_failed", "YouTube did not start the upload.")
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
        post_id = str(self._json_response(response).get("id") or "")
        if not post_id:
            raise ApiError(502, "social_publish_failed", "YouTube did not return a video id.")
        return PublishedPost(external_post_id=post_id, external_url=f"https://youtu.be/{post_id}")

    @staticmethod
    def _json_response(response: httpx.Response) -> dict[str, object]:
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = {}
            message = str(
                detail.get("message")
                or detail.get("error")
                or "The social platform rejected the publish request."
            )
            if response.status_code in {401, 403}:
                raise ApiError(
                    401,
                    "publish_token_expired",
                    "Publishing authorization expired. Refreshing the connection is required.",
                )
            raise ApiError(502, "social_publish_failed", message[:500])
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(
                502, "social_publish_failed", "The social platform returned an invalid response."
            ) from exc


def execute_publish_job(
    *,
    publishing_repository,
    render_repository,
    storage_signer,
    client: SocialPublishingClient,
    oauth_client: PublishOAuthClient | None = None,
    job_id,
    workspace_id,
) -> PublishJobRecord:
    job = publishing_repository.get_job(job_id=job_id, workspace_id=workspace_id)
    if job.status == PublishStatus.PUBLISHED:
        return job
    connection = publishing_repository.get_connection(
        connection_id=job.connection_id, workspace_id=workspace_id
    )
    if (
        oauth_client is not None
        and connection.token_expires_at is not None
        and connection.token_expires_at <= datetime.now(UTC) + timedelta(minutes=5)
    ):
        refreshed = oauth_client.refresh(connection)
        connection = publishing_repository.update_connection_tokens(
            connection=connection,
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token,
            token_expires_at=refreshed.token_expires_at,
        )
    export = render_repository.get_export(export_id=job.export_id, workspace_id=workspace_id)
    publishing_repository.update_job(job=job, status=PublishStatus.PUBLISHING, error_message=None)
    download = storage_signer.create_signed_download(
        bucket=export.storage_bucket, path=export.storage_path, expires_in_seconds=3600
    )
    try:
        try:
            result = client.publish(connection=connection, job=job, video_url=download.url)
        except ApiError as exc:
            if exc.code != "publish_token_expired" or oauth_client is None:
                raise
            refreshed = oauth_client.refresh(connection)
            connection = publishing_repository.update_connection_tokens(
                connection=connection,
                access_token=refreshed.access_token,
                refresh_token=refreshed.refresh_token,
                token_expires_at=refreshed.token_expires_at,
            )
            result = client.publish(connection=connection, job=job, video_url=download.url)
    except Exception as exc:
        message = exc.message if isinstance(exc, ApiError) else "Publishing failed unexpectedly."
        publishing_repository.update_job(
            job=job, status=PublishStatus.FAILED, error_message=message
        )
        raise
    return publishing_repository.update_job(
        job=job,
        status=PublishStatus.PUBLISHED,
        external_post_id=result.external_post_id,
        external_url=result.external_url,
        error_message=None,
    )
