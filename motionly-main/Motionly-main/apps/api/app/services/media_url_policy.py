from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import socket
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
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

_MANUAL_MEDIA_PROPERTY_KEYS = ("assetUrl", "src", "imageUrl")
_MANUAL_MEDIA_TYPES = {
    "broll": False,
    "graphic": False,
    "music": True,
    "sfx": True,
}
_CSS_BACKGROUND_PROPERTY_KEYS = (
    "background",
    "backgroundColor",
    "backgroundImage",
    "background_color",
    "background_image",
)
_CSS_URL_RE = re.compile(
    r"url\s*\(\s*(?:['\"](?P<quoted>[^'\"]*)['\"]|(?P<bare>[^)\s]+))\s*\)",
    re.IGNORECASE,
)
# This permits the color and gradient subset used by the editor while excluding
# quotes, escapes, URLs, and declarations that can make Chromium load a resource.
_SAFE_CSS_BACKGROUND_RE = re.compile(r"[A-Za-z0-9#(),.%+\-\s]*")


def sanitize_manual_element_properties(
    element_type: object,
    properties: object,
) -> dict[str, Any]:
    """Return a type-specific, non-networking manual-element property bag.

    The editor's historical ``properties`` bag was intentionally extensible.
    Keep non-media controls forward compatible, but normalize the media and CSS
    controls the renderers consume. Legacy URL-bearing CSS degrades to a safe
    visual fallback rather than becoming a Chromium network request.
    """
    if not isinstance(properties, dict):
        raise ValueError("Manual element properties must be an object.")

    sanitized = dict(properties)
    fallback = "#14b8a6" if element_type == "shape" else "transparent"
    for key in _CSS_BACKGROUND_PROPERTY_KEYS:
        if key not in sanitized:
            continue
        safe_value = sanitize_css_background(sanitized[key], fallback=fallback)
        if key in {"backgroundImage", "background_image"}:
            # The renderer consumes a single safe background declaration.
            sanitized.pop(key, None)
            sanitized["background"] = safe_value
        elif key == "background_color":
            sanitized.pop(key, None)
            sanitized["backgroundColor"] = safe_value
        else:
            sanitized[key] = safe_value

    allows_data_audio = _MANUAL_MEDIA_TYPES.get(str(element_type))
    for key in _MANUAL_MEDIA_PROPERTY_KEYS:
        if key not in sanitized:
            continue
        value = sanitized[key]
        if allows_data_audio is None:
            # A visual/text element must not retain a hidden generic media URL.
            sanitized.pop(key, None)
            continue
        if value is None or value == "":
            sanitized.pop(key, None)
            continue
        if not isinstance(value, str):
            raise ValueError("Manual element media references must be strings.")

    # ``imageUrl`` was used by a pre-release graphic palette. Preserve that
    # legacy payload only through the one graphic source field the renderers use.
    if element_type == "graphic" and "imageUrl" in sanitized:
        sanitized.setdefault("assetUrl", sanitized["imageUrl"])
        sanitized.pop("imageUrl", None)

    return sanitized


def sanitize_css_background(value: object, *, fallback: str = "transparent") -> str:
    """Allow only inert color/gradient CSS; never return a URL-bearing value."""
    if not isinstance(value, str):
        return fallback
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 1_000
        or not _SAFE_CSS_BACKGROUND_RE.fullmatch(candidate)
        or _CSS_URL_RE.search(candidate)
    ):
        return fallback
    return candidate


def manual_element_media_references(element: object) -> list[tuple[str, str, bool]]:
    """List typed URL-bearing manual properties for execution-time validation."""
    if not isinstance(element, dict):
        raise MediaUrlPolicyError("Manual media element is invalid.")
    properties = element.get("properties")
    if properties is None:
        return []
    if not isinstance(properties, dict):
        raise MediaUrlPolicyError("Manual media properties are invalid.")

    element_type = element.get("type")
    references: list[tuple[str, str, bool]] = []
    allows_data_audio = _MANUAL_MEDIA_TYPES.get(element_type)
    if allows_data_audio is not None:
        for key in _MANUAL_MEDIA_PROPERTY_KEYS:
            if key not in properties:
                continue
            value = properties[key]
            if value is None or value == "":
                continue
            if not isinstance(value, str):
                raise MediaUrlPolicyError("Manual media URL must be a string.")
            references.append((key, value, allows_data_audio))

    for key in _CSS_BACKGROUND_PROPERTY_KEYS:
        if key not in properties:
            continue
        value = properties[key]
        if not isinstance(value, str):
            raise MediaUrlPolicyError("Manual CSS background must be a string.")
        for reference in _css_url_references(value):
            references.append((key, reference, False))
    return references


def _css_url_references(value: str) -> tuple[str, ...]:
    references: list[str] = []
    for match in _CSS_URL_RE.finditer(value):
        reference = (match.group("quoted") or match.group("bare") or "").strip()
        if not reference:
            raise MediaUrlPolicyError("Manual CSS background URL is invalid.")
        references.append(reference)
    return tuple(references)


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
            for _key, value, allow_data_audio in manual_element_media_references(element):
                check(value, allow_data_audio=allow_data_audio)

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


@dataclass(frozen=True)
class _PinnedMediaTarget:
    request_url: str
    host_header: str
    sni_hostname: str | None


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
    """Open a media stream pinned to prevalidated addresses for every hop."""
    if max_redirects < 0 or max_redirects > 10:
        raise MediaUrlPolicyError("Media redirect limit is invalid.")
    factory = stream_factory or _httpx_pinned_stream
    current = url
    for redirect_count in range(max_redirects + 1):
        targets = _pin_media_targets(current, resolver=resolver)
        last_transport_error: httpx.TransportError | None = None
        redirected = False
        for target in targets:
            request_headers = {
                key: value for key, value in (headers or {}).items() if key.lower() != "host"
            }
            request_headers["Host"] = target.host_header
            extensions = (
                {"sni_hostname": target.sni_hostname}
                if target.sni_hostname is not None
                else None
            )
            stack = ExitStack()
            try:
                response = stack.enter_context(
                    factory(
                        "GET",
                        target.request_url,
                        headers=request_headers,
                        timeout=timeout,
                        follow_redirects=False,
                        extensions=extensions,
                    )
                )
            except httpx.TransportError as exc:
                stack.close()
                last_transport_error = exc
                continue

            with stack:
                if getattr(response, "status_code", 200) not in _REDIRECT_STATUSES:
                    yield response
                    return
                location = response.headers.get("location")
                if not location:
                    raise MediaUrlPolicyError("Media redirect is missing a destination.")
                if redirect_count >= max_redirects:
                    raise MediaUrlPolicyError("Media redirect limit exceeded.")
                current = urljoin(current, location)
                redirected = True
            break

        if redirected:
            continue
        if last_transport_error is not None:
            raise last_transport_error
        raise MediaUrlPolicyError("Media URL host could not be reached.")
    raise MediaUrlPolicyError("Media redirect limit exceeded.")


@contextmanager
def _httpx_pinned_stream(method: str, url: str, **kwargs: Any) -> Iterator[httpx.Response]:
    extensions = kwargs.pop("extensions", None)
    with httpx.Client(trust_env=False) as client:
        with client.stream(method, url, extensions=extensions, **kwargs) as response:
            yield response


def _pin_media_targets(
    value: str,
    *,
    resolver: Resolver | None,
) -> tuple[_PinnedMediaTarget, ...]:
    validate_outbound_media_url(value, resolve_dns=False)
    parsed = urlsplit(value)
    hostname = parsed.hostname.rstrip(".").lower() if parsed.hostname else ""
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    literal = _ip_address(hostname)
    if literal is not None:
        _validate_public_ip(literal)
        addresses = (literal,)
        sni_hostname = None
    else:
        _validate_hostname(hostname)
        addresses = _validate_dns(hostname, port, resolver, require_dns=True)
        sni_hostname = hostname

    original_host = f"[{hostname}]" if ":" in hostname else hostname
    host_header = f"{original_host}:{parsed.port}" if parsed.port is not None else original_host
    targets: list[_PinnedMediaTarget] = []
    for address in addresses:
        address_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        request_netloc = (
            f"{address_host}:{parsed.port}" if parsed.port is not None else address_host
        )
        targets.append(
            _PinnedMediaTarget(
                request_url=parsed._replace(netloc=request_netloc).geturl(),
                host_header=host_header,
                sni_hostname=sni_hostname,
            )
        )
    return tuple(targets)


def decode_bounded_audio_data_url(value: str) -> bytes:
    return _decode_bounded_audio_data_url(value)


def _validate_hostname(hostname: str) -> None:
    if not hostname or len(hostname) > 253 or "%" in hostname:
        raise MediaUrlPolicyError("Media URL host is invalid.")
    if hostname in _LOCAL_HOST_NAMES or hostname.endswith(_LOCAL_HOST_SUFFIXES):
        raise MediaUrlPolicyError("Local media hosts are not allowed.")
    if _looks_like_legacy_ipv4_hostname(hostname):
        raise MediaUrlPolicyError("Media URL host is an invalid numeric address.")
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
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    lookup = resolver or socket.getaddrinfo
    try:
        records = lookup(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        if require_dns:
            raise MediaUrlPolicyError("Media URL host could not be resolved.") from exc
        return ()
    raw_addresses = [
        str(record[4][0]).split("%", 1)[0]
        for record in records
        if len(record) >= 5 and isinstance(record[4], tuple) and record[4]
    ]
    if not raw_addresses:
        if require_dns:
            raise MediaUrlPolicyError("Media URL host could not be resolved.")
        return ()
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for raw_address in raw_addresses:
        parsed = _ip_address(raw_address)
        if parsed is None:
            raise MediaUrlPolicyError("Media URL resolved to an invalid address.")
        _validate_public_ip(parsed)
        if parsed not in addresses:
            addresses.append(parsed)
    return tuple(addresses)


def _ip_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return _legacy_ipv4_address(value)


def _legacy_ipv4_address(value: str) -> ipaddress.IPv4Address | None:
    parts = value.split(".")
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        return None
    numbers: list[int] = []
    try:
        for part in parts:
            lowered = part.lower()
            if lowered.startswith("0x"):
                digits, base = part[2:], 16
            elif len(part) > 1 and part.startswith("0"):
                digits, base = part[1:], 8
            else:
                digits, base = part, 10
            if not digits:
                return None
            numbers.append(int(digits, base))
    except ValueError:
        return None
    if any(number > 255 for number in numbers[:-1]):
        return None
    if numbers[-1] >= 256 ** (5 - len(numbers)):
        return None
    numeric = numbers[-1]
    for index, number in enumerate(numbers[:-1]):
        numeric += number * 256 ** (3 - index)
    return ipaddress.IPv4Address(numeric)


def _looks_like_legacy_ipv4_hostname(hostname: str) -> bool:
    parts = hostname.split(".")
    return all(
        part.isdigit()
        or (
            part.lower().startswith("0x")
            and len(part) > 2
            and all(character in "0123456789abcdef" for character in part[2:].lower())
        )
        for part in parts
    )


def _validate_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        not address.is_global
        or address.is_loopback
        or address.is_unspecified
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
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
