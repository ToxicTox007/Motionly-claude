from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import httpx

_MAX_DATA_AUDIO_BYTES = 1_048_576
_MAX_URL_LENGTH = 8_192
_MAX_REDIRECTS = 3
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_STORAGE_BUCKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".lan", ".home")
_LOCAL_HOST_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


class MediaUrlPolicyError(ValueError):
    """A media reference is unsafe. Messages intentionally never contain the URL."""


@dataclass(frozen=True)
class ValidatedMediaUrl:
    value: str
    kind: str


Resolver = Callable[..., list[tuple[Any, ...]]]


def validate_outbound_media_url(
    value: str,
    *,
    allow_supabase: bool = False,
    allow_fixture: bool = False,
    allow_data_audio: bool = False,
    resolve_dns: bool = True,
    require_dns: bool = False,
    resolver: Resolver | None = None,
) -> ValidatedMediaUrl:
    """Validate one render media reference without returning or logging secrets."""
    if not isinstance(value, str) or not value or len(value) > _MAX_URL_LENGTH:
        raise MediaUrlPolicyError("Media URL is missing or too long.")
    if any(ord(character) < 32 or character.isspace() for character in value):
        raise MediaUrlPolicyError("Media URL contains invalid characters.")

    if value.startswith("supabase://"):
        if not allow_supabase:
            raise MediaUrlPolicyError("Internal media references are not allowed here.")
        _validate_supabase_reference(value)
        return ValidatedMediaUrl(value=value, kind="supabase")

    if value.startswith("fixture://"):
        if not allow_fixture:
            raise MediaUrlPolicyError("Fixture media references are not allowed here.")
        _validate_fixture_reference(value)
        return ValidatedMediaUrl(value=value, kind="fixture")

    if value.startswith("data:"):
        if not allow_data_audio:
            raise MediaUrlPolicyError("Data media URLs are not allowed here.")
        _decode_bounded_audio_data_url(value)
        return ValidatedMediaUrl(value=value, kind="data_audio")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise MediaUrlPolicyError("Media URL is malformed.") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise MediaUrlPolicyError("Media URL scheme is not supported.")
    if not parsed.netloc or not parsed.hostname:
        raise MediaUrlPolicyError("Media URL host is missing.")
    if parsed.username is not None or parsed.password is not None:
        raise MediaUrlPolicyError("Credential-bearing media URLs are not allowed.")
    if port is not None and not 1 <= port <= 65_535:
        raise MediaUrlPolicyError("Media URL port is invalid.")

    hostname = parsed.hostname.rstrip(".").lower()
    literal = _ip_address(hostname)
    if literal is not None:
        _validate_public_ip(literal)
    else:
        _validate_hostname(hostname)
        if resolve_dns:
            _validate_dns(
                hostname,
                port or (443 if parsed.scheme.lower() == "https" else 80),
                resolver,
                require_dns,
            )
    return ValidatedMediaUrl(value=value, kind="http")


def validate_render_media_manifest(
    render_props: dict[str, Any],
    *,
    allow_fixture: bool = False,
    resolve_dns: bool = True,
    require_dns: bool = False,
    resolver: Resolver | None = None,
) -> None:
    """Validate every media reference Remotion or FFmpeg can consume."""

    def check(
        value: object,
        *,
        allow_supabase: bool = True,
        allow_data_audio: bool = False,
    ) -> None:
        if value is None or value == "":
            return
        if not isinstance(value, str):
            raise MediaUrlPolicyError("Media URL must be a string.")
        validate_outbound_media_url(
            value,
            allow_supabase=allow_supabase,
            allow_fixture=allow_fixture,
            allow_data_audio=allow_data_audio,
            resolve_dns=resolve_dns,
            require_dns=require_dns,
            resolver=resolver,
        )

    source_video = render_props.get("sourceVideo")
    if isinstance(source_video, dict):
        check(source_video.get("src"))

    assets = render_props.get("assets")
    if isinstance(assets, list):
        for asset in assets:
            if isinstance(asset, dict) and asset.get("kind") in {"video", "audio", "image"}:
                check(asset.get("src"))

    edit_plan = render_props.get("editPlan")
    elements = edit_plan.get("manual_elements") if isinstance(edit_plan, dict) else None
    if isinstance(elements, list):
        for element in elements:
            if (
                not isinstance(element, dict)
                or element.get("type") not in {"broll", "music", "sfx"}
            ):
                continue
            properties = element.get("properties")
            if not isinstance(properties, dict):
                continue
            for key in ("assetUrl", "src"):
                if key in properties:
                    check(
                        properties.get(key),
                        allow_data_audio=element.get("type") in {"music", "sfx"},
                    )

    music_selection = render_props.get("musicSelection")
    track = music_selection.get("track") if isinstance(music_selection, dict) else None
    if isinstance(track, dict):
        for key in ("full_track_url", "preview_url"):
            check(track.get(key))
        metadata = track.get("raw_metadata")
        if isinstance(metadata, dict):
            for key in ("audio_url", "download_url", "preview_url", "src"):
                check(metadata.get(key))
            sfx_urls = metadata.get("sfx_urls")
            if isinstance(sfx_urls, list):
                for item in sfx_urls:
                    check(item, allow_data_audio=True)
            for bucket_key, path_key in (
                ("audio_bucket", "audio_path"),
                ("storage_bucket", "storage_path"),
                ("bucket", "path"),
            ):
                bucket = metadata.get(bucket_key)
                path = metadata.get(path_key)
                if bucket is None and path is None:
                    continue
                if not isinstance(bucket, str) or not isinstance(path, str):
                    raise MediaUrlPolicyError("Internal media reference is invalid.")
                check(f"supabase://{bucket}/{path}")

    stems = render_props.get("sourceStems")
    if isinstance(stems, dict):
        for stem_name in ("vocals", "no_vocals"):
            stem = stems.get(stem_name)
            if isinstance(stem, dict):
                check(stem.get("src"))
                bucket = stem.get("bucket")
                path = stem.get("path")
                if bucket is not None or path is not None:
                    if not isinstance(bucket, str) or not isinstance(path, str):
                        raise MediaUrlPolicyError("Internal media reference is invalid.")
                    check(f"supabase://{bucket}/{path}")

    matte = render_props.get("segmentationMatte")
    if isinstance(matte, dict):
        check(matte.get("src"))
        bucket = matte.get("bucket")
        path = matte.get("path")
        if bucket is not None or path is not None:
            if not isinstance(bucket, str) or not isinstance(path, str):
                raise MediaUrlPolicyError("Internal media reference is invalid.")
            check(f"supabase://{bucket}/{path}")


@contextmanager
def safe_http_stream(
    url: str,
    *,
    timeout: float,
    max_redirects: int = _MAX_REDIRECTS,
    headers: dict[str, str] | None = None,
    resolver: Resolver | None = None,
    stream_factory: Callable[..., Any] | None = None,
) -> Iterator[httpx.Response]:
    """Open a validated media stream with explicit, revalidated redirects."""
    if max_redirects < 0 or max_redirects > 10:
        raise MediaUrlPolicyError("Media redirect limit is invalid.")
    factory = stream_factory or httpx.stream
    current = url
    for redirect_count in range(max_redirects + 1):
        validate_outbound_media_url(
            current,
            resolve_dns=True,
            require_dns=True,
            resolver=resolver,
        )
        with factory(
            "GET",
            current,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        ) as response:
            if getattr(response, "status_code", 200) not in _REDIRECT_STATUSES:
                yield response
                return
            location = response.headers.get("location")
            if not location:
                raise MediaUrlPolicyError("Media redirect is missing a destination.")
            if redirect_count >= max_redirects:
                raise MediaUrlPolicyError("Media redirect limit exceeded.")
            current = urljoin(current, location)
    raise MediaUrlPolicyError("Media redirect limit exceeded.")


def decode_bounded_audio_data_url(value: str) -> bytes:
    return _decode_bounded_audio_data_url(value)


def _validate_hostname(hostname: str) -> None:
    if not hostname or len(hostname) > 253 or "%" in hostname:
        raise MediaUrlPolicyError("Media URL host is invalid.")
    if hostname in _LOCAL_HOST_NAMES or hostname.endswith(_LOCAL_HOST_SUFFIXES):
        raise MediaUrlPolicyError("Local media hosts are not allowed.")
    if "." not in hostname and _ip_address(hostname) is None:
        raise MediaUrlPolicyError("Single-label media hosts are not allowed.")
    labels = hostname.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        raise MediaUrlPolicyError("Media URL host is invalid.")


def _validate_dns(
    hostname: str,
    port: int,
    resolver: Resolver | None,
    require_dns: bool,
) -> None:
    lookup = resolver or socket.getaddrinfo
    try:
        records = lookup(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        if require_dns:
            raise MediaUrlPolicyError("Media URL host could not be resolved.") from exc
        return
    addresses = {
        record[4][0]
        for record in records
        if len(record) >= 5 and isinstance(record[4], tuple) and record[4]
    }
    if not addresses:
        if require_dns:
            raise MediaUrlPolicyError("Media URL host could not be resolved.")
        return
    for address in addresses:
        parsed = _ip_address(str(address).split("%", 1)[0])
        if parsed is None:
            raise MediaUrlPolicyError("Media URL resolved to an invalid address.")
        _validate_public_ip(parsed)


def _ip_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _validate_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        not address.is_global
        or address.is_loopback
        or address.is_unspecified
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    ):
        raise MediaUrlPolicyError("Media URL address is not publicly routable.")


def _validate_supabase_reference(value: str) -> None:
    parsed = urlsplit(value)
    bucket = parsed.netloc
    path = unquote(parsed.path.lstrip("/"))
    if parsed.query or parsed.fragment or not _STORAGE_BUCKET_RE.fullmatch(bucket):
        raise MediaUrlPolicyError("Internal media reference is invalid.")
    if not path or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise MediaUrlPolicyError("Internal media reference is invalid.")


def _validate_fixture_reference(value: str) -> None:
    parsed = urlsplit(value)
    if not parsed.netloc or not parsed.path or parsed.query or parsed.fragment:
        raise MediaUrlPolicyError("Fixture media reference is invalid.")


def _decode_bounded_audio_data_url(value: str) -> bytes:
    prefix = "data:audio/wav;base64,"
    if not value.startswith(prefix):
        raise MediaUrlPolicyError("Only bounded WAV data audio is supported.")
    encoded = value.removeprefix(prefix)
    if not encoded or len(encoded) > ((_MAX_DATA_AUDIO_BYTES + 2) // 3) * 4:
        raise MediaUrlPolicyError("Data audio exceeds the allowed size.")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MediaUrlPolicyError("Data audio is malformed.") from exc
    if len(decoded) > _MAX_DATA_AUDIO_BYTES or not decoded.startswith(b"RIFF"):
        raise MediaUrlPolicyError("Data audio is not a supported WAV payload.")
    return decoded
