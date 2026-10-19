"""Task 5: one public origin.

Browsers reach Next.js at ``<origin>/``, FastAPI at ``<origin>/api`` and
Supabase at ``<origin>/supabase``; services talk to each other over the private
network. The API therefore signs browser links with the public Supabase URL and
worker links with the internal one (N12).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
import yaml

from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.storage import StorageSigner

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"
ROUTES = INFRA / "Caddyfile.routes"
ROUTES_MOUNT = "./Caddyfile.routes:/etc/caddy/routes.caddy:ro"
ORIGIN = "https://d111abcdef8.cloudfront.net"
INTERNAL_SUPABASE = "http://supabase-gateway:8000"
PUBLIC_SUPABASE = f"{ORIGIN}/supabase"


class _ComposeLoader(yaml.SafeLoader):
    """Understands Compose's !override and !reset merge tags."""


for _tag in ("!override", "!reset"):
    _ComposeLoader.add_constructor(
        _tag,
        lambda loader, node: (
            loader.construct_sequence(node)
            if isinstance(node, yaml.SequenceNode)
            else loader.construct_mapping(node)
            if isinstance(node, yaml.MappingNode)
            else loader.construct_scalar(node)
        ),
    )


def _yaml(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)


def _fake_storage_signing(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> httpx.Response:
        calls.append(url)
        request = httpx.Request("POST", url)
        if "/object/upload/sign/" in url:
            payload = {"url": "/object/upload/sign/raw-uploads/a/b.mp4?token=up"}
        else:
            payload = {"signedURL": "/object/sign/raw-uploads/a/b.mp4?token=down"}
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr("app.services.storage.httpx.post", fake_post)


def test_browser_links_use_the_public_supabase_url_and_worker_links_stay_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _fake_storage_signing(monkeypatch, calls)
    signer = StorageSigner(
        base_url=f"{ORIGIN}/api",
        supabase_url=INTERNAL_SUPABASE,
        public_supabase_url=PUBLIC_SUPABASE,
        service_role_key="service-role",
    )

    upload = signer.create_signed_upload(
        bucket="raw-uploads", path="a/b.mp4", mime_type="video/mp4", expires_in_seconds=60
    )
    download = signer.create_signed_download(
        bucket="raw-uploads", path="a/b.mp4", expires_in_seconds=60
    )
    worker = signer.create_signed_worker_download(
        bucket="raw-uploads", path="a/b.mp4", expires_in_seconds=60
    )

    assert upload.url == (
        f"{PUBLIC_SUPABASE}/storage/v1/object/upload/sign/raw-uploads/a/b.mp4?token=up"
    )
    signed_path = "/storage/v1/object/sign/raw-uploads/a/b.mp4?token=down"
    assert download.url == f"{PUBLIC_SUPABASE}{signed_path}"
    assert worker.url == f"{INTERNAL_SUPABASE}{signed_path}"
    # Signing itself always goes over the private network.
    assert len(calls) == 3
    assert all(url.startswith(f"{INTERNAL_SUPABASE}/storage/v1/object/") for url in calls)


def test_without_a_public_url_browser_links_use_the_supabase_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_storage_signing(monkeypatch, [])
    signer = StorageSigner(
        base_url="http://127.0.0.1:8000",
        supabase_url="http://127.0.0.1:54321",
        service_role_key="service-role",
    )

    download = signer.create_signed_download(
        bucket="raw-uploads", path="a/b.mp4", expires_in_seconds=60
    )

    assert download.url.startswith("http://127.0.0.1:54321/storage/v1/object/sign/")


def test_settings_expose_the_supabase_url_browsers_use() -> None:
    secrets = {
        "SUPABASE_JWT_SECRET": "jwt-secret",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role",
        # Independent of whatever the surrounding environment enables.
        "SKIP_API_AUTH": "false",
    }
    internal_only = Settings(
        _env_file=None, APP_ENV="test", SUPABASE_URL="http://127.0.0.1:54321", **secrets
    )
    split = Settings(
        _env_file=None,
        APP_ENV="test",
        SUPABASE_URL=INTERNAL_SUPABASE,
        SUPABASE_PUBLIC_URL="http://127.0.0.1:18080/supabase/",
        **secrets,
    )

    assert internal_only.browser_supabase_url == "http://127.0.0.1:54321"
    assert split.browser_supabase_url == "http://127.0.0.1:18080/supabase"


def test_the_api_signs_browser_links_with_the_public_supabase_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPABASE_URL", INTERNAL_SUPABASE)
    monkeypatch.setenv("SUPABASE_PUBLIC_URL", PUBLIC_SUPABASE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-for-test")
    monkeypatch.setenv("FORCE_LOCAL_MEDIA_STORAGE", "false")
    get_settings.cache_clear()
    try:
        app = create_app()
    finally:
        get_settings.cache_clear()

    assert app.state.storage_signer.supabase_url == INTERNAL_SUPABASE
    assert app.state.storage_signer.public_supabase_url == PUBLIC_SUPABASE


def _caddy_sites(text: str) -> list[str]:
    """Top-level site addresses; the global options block ``{`` is not a site."""
    sites: list[str] = []
    depth = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if depth == 0 and line.endswith("{") and line != "{":
            sites.append(line[:-1].strip())
        depth += line.count("{") - line.count("}")
    return sites


def test_one_shared_route_file_maps_paths_to_services() -> None:
    code = "\n".join(
        line
        for line in ROUTES.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )

    handlers = re.findall(
        r"^(handle(?:_path [^\s{]+)?) \{\s*reverse_proxy (\S+) \{", code, flags=re.MULTILINE
    )

    assert handlers == [
        ("handle_path /api/*", "api:8000"),
        ("handle_path /supabase/*", "supabase-gateway:8000"),
        ("handle", "web:3000"),
    ]


@pytest.mark.parametrize(
    ("caddyfile", "compose_file", "site"),
    [
        ("Caddyfile", "docker-compose.gpu.yml", "http://{$APP_DOMAIN}"),
        ("Caddyfile.cpu-stage", "docker-compose.cpu-stage.yml", ":80"),
        (
            "Caddyfile.local-audit",
            "docker-compose.local-audit.yml",
            "{$APP_DOMAIN:app.audit.localhost}",
        ),
    ],
)
def test_every_ingress_serves_one_site_through_the_shared_routes(
    caddyfile: str, compose_file: str, site: str
) -> None:
    text = (INFRA / caddyfile).read_text(encoding="utf-8")

    assert _caddy_sites(text) == [site]
    assert "import /etc/caddy/routes.caddy" in text
    assert "reverse_proxy" not in text
    ingress = _yaml(INFRA / compose_file)["services"]["ingress"]
    assert ROUTES_MOUNT in ingress["volumes"]
    assert "API_DOMAIN" not in json.dumps(ingress)
    assert "SUPABASE_DOMAIN" not in json.dumps(ingress)


def test_the_cpu_stage_has_one_loopback_origin() -> None:
    stack = json.loads((ROOT / "tests" / "cpu-stage" / "stack.json").read_text(encoding="utf-8"))

    # HTTPS through the CloudFront simulator (test_origin_guard.py).
    assert stack["public_origin"] == "https://127.0.0.1:18443"
    assert "domains" not in stack


def test_templates_and_setup_scripts_describe_one_public_origin() -> None:
    files = [
        ROOT / ".env.example",
        ROOT / "apps" / "web" / ".env.example",
        ROOT / "setup-local-env.sh",
        ROOT / "setup-local-env.ps1",
        *sorted(INFRA.glob("*.env.example")),
        *sorted((INFRA / "compose-host").glob("*.env.example")),
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        for retired in ("API_DOMAIN", "SUPABASE_DOMAIN", "NEXT_PUBLIC_"):
            assert retired not in text, f"{path.relative_to(ROOT)} still mentions {retired}"
    for path in (
        INFRA / "production-compose.env.example",
        INFRA / "development-compose.env.example",
        ROOT / "apps" / "web" / ".env.example",
    ):
        assert re.search(r"^PUBLIC_ORIGIN=", path.read_text(encoding="utf-8"), re.M), path
