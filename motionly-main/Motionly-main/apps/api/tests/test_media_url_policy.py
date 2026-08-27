import socket

import httpx
import pytest

from app.schemas.plans import ManualElement
from app.services.media_url_policy import (
    MediaUrlPolicyError,
    safe_http_stream,
    validate_outbound_media_url,
    validate_render_media_manifest,
)


def _public_lookup(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0/media.mp4",
        "http://10.1.2.3/media.mp4",
        "http://127.0.0.1/media.mp4",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.0.2.20/media.mp4",
        "http://224.0.0.1/media.mp4",
        "http://240.0.0.1/media.mp4",
        "http://2130706433/media.mp4",
        "http://0177.0.0.1/media.mp4",
        "http://127.1/media.mp4",
        "http://0x7f000001/media.mp4",
        "http://[::]/media.mp4",
        "http://[::1]/media.mp4",
        "http://[::ffff:127.0.0.1]/media.mp4",
        "http://[fc00::1]/media.mp4",
        "http://[fe80::1]/media.mp4",
        "http://[fec0::1]/media.mp4",
        "http://[ff02::1]/media.mp4",
        "http://[2001:db8::1]/media.mp4",
    ],
)
def test_media_url_policy_rejects_non_public_literal_addresses(url: str) -> None:
    with pytest.raises(MediaUrlPolicyError):
        validate_outbound_media_url(url, resolve_dns=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/media.mp4",
        "http://player.localhost/media.mp4",
        "http://service.internal/media.mp4",
        "http://media/media.mp4",
        "file:///etc/passwd",
        "ftp://media.example.com/video.mp4",
        "https://user:secret@media.example.com/video.mp4",
    ],
)
def test_media_url_policy_rejects_local_names_credentials_and_schemes(url: str) -> None:
    with pytest.raises(MediaUrlPolicyError):
        validate_outbound_media_url(url, resolve_dns=False)


def test_media_url_policy_checks_every_dns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def private_lookup(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", private_lookup)
    with pytest.raises(MediaUrlPolicyError):
        validate_outbound_media_url("https://media.example.com/video.mp4")

    def mixed_lookup(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", 443, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", mixed_lookup)
    with pytest.raises(MediaUrlPolicyError):
        validate_outbound_media_url("https://media.example.com/video.mp4")

    def site_local_lookup(*_args, **_kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fec0::1", 443, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", site_local_lookup)
    with pytest.raises(MediaUrlPolicyError):
        validate_outbound_media_url("https://media.example.com/video.mp4")


def test_media_url_policy_allows_public_https_and_ipv6() -> None:
    public_lookup = _public_lookup
    assert validate_outbound_media_url(
        "https://media.example.com/video.mp4?signature=secret",
        resolver=public_lookup,
    ).kind == "http"
    assert validate_outbound_media_url("https://[2606:4700:4700::1111]/video.mp4").kind == "http"
    assert validate_outbound_media_url(
        "http://0x08080808/video.mp4", resolve_dns=False
    ).kind == "http"


def test_media_url_policy_preserves_strict_internal_and_bounded_audio_refs() -> None:
    internal = "supabase://raw-uploads/workspaces/ws/projects/project/video.mp4"
    assert validate_outbound_media_url(
        internal,
        allow_supabase=True,
        resolve_dns=False,
    ).kind == "supabase"
    with pytest.raises(MediaUrlPolicyError):
        validate_outbound_media_url(
            "supabase://raw-uploads/workspaces/ws/../other/video.mp4",
            allow_supabase=True,
            resolve_dns=False,
        )

    wav = "data:audio/wav;base64,UklGRg=="
    assert validate_outbound_media_url(wav, allow_data_audio=True).kind == "data_audio"
    with pytest.raises(MediaUrlPolicyError):
        validate_outbound_media_url("data:text/plain;base64,UklGRg==", allow_data_audio=True)


def test_manifest_policy_covers_manual_assets_music_sfx_and_internal_sources() -> None:
    public_lookup = _public_lookup
    props = {
        "sourceVideo": {"src": "supabase://raw-uploads/workspaces/ws/source.mp4"},
        "assets": [{"kind": "video", "src": "https://media.example.com/broll.mp4"}],
        "editPlan": {
            "manual_elements": [
                {
                    "type": "sfx",
                    "properties": {"assetUrl": "data:audio/wav;base64,UklGRg=="},
                },
                {
                    "type": "broll",
                    "properties": {"src": "https://media.example.com/manual.mp4"},
                },
            ]
        },
        "musicSelection": {
            "track": {
                "raw_metadata": {
                    "audio_url": "https://media.example.com/music.mp3",
                    "sfx_urls": ["https://media.example.com/pop.wav"],
                }
            }
        },
        "segmentationMatte": {"src": "supabase://mattes/workspaces/ws/matte.webm"},
    }
    validate_render_media_manifest(props, resolver=public_lookup)
    props["editPlan"]["manual_elements"][1]["properties"]["src"] = (
        "http://169.254.169.254/latest/meta-data/"
    )
    with pytest.raises(MediaUrlPolicyError):
        validate_render_media_manifest(props, resolver=public_lookup)


class _Response:
    def __init__(self, status_code: int, location: str | None = None) -> None:
        self.status_code = status_code
        self.headers = {"location": location} if location else {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class _StreamFactory:
    def __init__(self, responses: list[_Response | httpx.TransportError]) -> None:
        self.responses = iter(responses)
        self.urls: list[str] = []
        self.kwargs: list[dict] = []

    def __call__(self, _method, url, **kwargs):
        assert kwargs["follow_redirects"] is False
        self.urls.append(url)
        self.kwargs.append(kwargs)
        response = next(self.responses)
        if isinstance(response, httpx.TransportError):
            raise response
        return response


def test_safe_http_stream_pins_validated_dns_answers_and_fails_over() -> None:
    calls = 0

    def rebinding_lookup(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 443)),
        ]

    factory = _StreamFactory(
        [httpx.ConnectError("first vetted address unavailable"), _Response(200)]
    )
    with safe_http_stream(
        "https://media.example.com/video.mp4?token=secret",
        timeout=1,
        resolver=rebinding_lookup,
        stream_factory=factory,
    ) as response:
        assert response.status_code == 200

    assert calls == 1
    assert factory.urls == [
        "https://93.184.216.34/video.mp4?token=secret",
        "https://93.184.216.35/video.mp4?token=secret",
    ]
    assert all(item["headers"]["Host"] == "media.example.com" for item in factory.kwargs)
    assert all(
        item["extensions"] == {"sni_hostname": "media.example.com"}
        for item in factory.kwargs
    )


def test_safe_http_stream_revalidates_redirect_destination() -> None:
    factory = _StreamFactory([_Response(302, "http://127.0.0.1/private.mp4")])
    public_lookup = _public_lookup
    with pytest.raises(MediaUrlPolicyError):
        with safe_http_stream(
            "https://media.example.com/start.mp4?token=do-not-log",
            timeout=1,
            resolver=public_lookup,
            stream_factory=factory,
        ):
            pass
    assert factory.urls == ["https://93.184.216.34/start.mp4?token=do-not-log"]


def test_safe_http_stream_bounds_redirects_without_leaking_query() -> None:
    factory = _StreamFactory(
        [
            _Response(302, "/second.mp4?token=hidden"),
            _Response(302, "/third.mp4?token=still-hidden"),
        ]
    )
    public_lookup = _public_lookup
    with pytest.raises(MediaUrlPolicyError) as exc_info:
        with safe_http_stream(
            "https://media.example.com/start.mp4?token=secret",
            timeout=1,
            max_redirects=1,
            resolver=public_lookup,
            stream_factory=factory,
        ):
            pass
    assert "secret" not in str(exc_info.value)
    assert "hidden" not in str(exc_info.value)


def test_manual_graphics_and_css_backgrounds_are_typed_sanitized_and_validated() -> None:
    legacy_graphic = ManualElement(
        id="legacy-graphic",
        type="graphic",
        name="Legacy graphic",
        zone="canvas",
        start_ms=0,
        end_ms=1_000,
        properties={
            "imageUrl": "https://media.example.com/legacy.png",
            "background": "url(http://169.254.169.254/latest/meta-data/)",
        },
    )
    # Old palettes used imageUrl, while raw CSS URLs never reach a renderer.
    assert legacy_graphic.properties["assetUrl"] == "https://media.example.com/legacy.png"
    assert "imageUrl" not in legacy_graphic.properties
    assert legacy_graphic.properties["background"] == "transparent"

    props = {
        "editPlan": {
            "manual_elements": [
                {
                    "type": "graphic",
                    "properties": {"assetUrl": "https://media.example.com/graphic.png"},
                },
                {
                    "type": "overlay",
                    "properties": {
                        "background": "linear-gradient(135deg,#0f172a,#f59e0b)",
                    },
                },
            ]
        }
    }
    validate_render_media_manifest(props, resolver=_public_lookup)

    props["editPlan"]["manual_elements"][0]["properties"]["assetUrl"] = (
        "http://169.254.169.254/latest/meta-data/"
    )
    with pytest.raises(MediaUrlPolicyError):
        validate_render_media_manifest(props, resolver=_public_lookup)

    props["editPlan"]["manual_elements"][0]["properties"]["assetUrl"] = (
        "https://media.example.com/graphic.png"
    )
    props["editPlan"]["manual_elements"][1]["properties"]["background"] = (
        "url('http://127.0.0.1/private.png')"
    )
    with pytest.raises(MediaUrlPolicyError):
        validate_render_media_manifest(props, resolver=_public_lookup)
