import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.api.internal_jobs import require_worker_token
from app.core.config import get_settings
from app.core.middleware import security_headers_middleware
from app.core.security import constant_time_token_matches
from app.main import _validate_production_settings
from app.services.queue import StockAssetEnrichmentQueue

ROOT = Path(__file__).resolve().parents[3]


def _production_settings(*, process_role: str = "api", **updates):
    values = {
        "app_env": "production",
        "process_role": process_role,
        "api_public_base_url": "https://api.example.com",
        "api_cors_origins": "https://app.example.com",
        "supabase_url": "https://project.supabase.co",
        "supabase_public_url": "https://app.example.com/supabase",
        "supabase_jwt_secret": "jwt-secret",
        "supabase_service_role_key": "service-role",
        "redis_url": "redis://redis.example.com:6379",
        "worker_api_token": "worker-only-token",
        "admin_api_token": None,
        "metrics_bearer_token": "metrics-token",
        "skip_api_auth": False,
        "allow_fixture_providers": False,
        "model_gateway_enabled": True,
        "temporal_tls_enabled": True,
        "stem_separation_provider": "demucs",
        "transcription_provider": "groq",
        "groq_api_key": "groq-key",
        "edit_planner_provider": "openrouter",
        "edit_planner_model": "openai/gpt-4.1-mini",
        "edit_planner_model_revision": "openai/gpt-4.1-mini-2025-04-14",
        "edit_planner_input_cost_per_million_usd": 2.0,
        "edit_planner_output_cost_per_million_usd": 2.0,
        "transcription_model_revision": "whisper-large-v3-turbo-2025-02-01",
        "transcription_estimated_cost_per_request_usd": 0.25,
        "openrouter_api_key": "openrouter-key",
        "visual_intelligence_provider": "local_ml",
        "audio_ml_provider": "local_ml",
        "runway_api_key": "runway-key",
        "music_provider": "licensed_catalog",
        "stock_video_provider": "pexels_pixabay",
        "pexels_api_key": "pexels-key",
        "pixabay_api_key": "pixabay-key",
        "billing_enabled": False,
        "billing_mode": "disabled",
        "force_local_media_storage": False,
    }
    values.update(updates)
    return get_settings().model_copy(update=values)


def test_production_requires_the_https_supabase_url_browsers_use() -> None:
    """Signed media links are opened by browsers, so they need the public URL (N12).

    Python workers run jobs through the same app (app.workers.domain_job), so
    the links they sign need it too. The Compose API runs self-hosted Temporal,
    whose validation path returns early, so that path is covered explicitly.
    """
    self_hosted_api = {
        "temporal_deployment_mode": "self_hosted",
        "temporal_address": "temporal:7233",
        "temporal_namespace": "motionly",
        "temporal_task_queue": "motionly-orchestration",
        "temporal_server_root_ca_path": "/run/secrets/temporal_ca_cert",
        "temporal_client_cert_path": "/run/secrets/temporal_api_cert",
        "temporal_client_private_key_path": "/run/secrets/temporal_api_key",
        "temporal_server_name": "temporal",
        "temporal_client_role": "api",
        "temporal_identity": "motionly-api",
        "temporal_tls_enabled": True,
    }
    for overrides in ({"process_role": "api"}, {"process_role": "worker"}, self_hosted_api):
        _validate_production_settings(_production_settings(**overrides))
        with pytest.raises(RuntimeError, match="SUPABASE_PUBLIC_URL"):
            _validate_production_settings(
                _production_settings(**overrides, supabase_public_url=None)
            )
        for value in (
            "http://app.example.com/supabase",
            "https://localhost/supabase",
            "https://127.0.0.1/supabase",
            "https://app.example.invalid/supabase",
        ):
            with pytest.raises(RuntimeError, match="SUPABASE_PUBLIC_URL"):
                _validate_production_settings(
                    _production_settings(**overrides, supabase_public_url=value)
                )


def _custom_gateway_route(*, capability: str, revision: str = "model-2026-07-14") -> dict:
    return {
        "route_id": f"custom-{capability}",
        "capability": capability,
        "adapter": "openai_compatible",
        "provider": "private-gpu",
        "model": f"{capability}-model",
        "model_revision": revision,
        "fixed_cost_usd": 0.1,
    }


def test_production_gateway_rejects_empty_and_incomplete_routes() -> None:
    with pytest.raises(RuntimeError, match="missing capabilities: planning, transcription"):
        _validate_production_settings(
            _production_settings(
                groq_api_key=None,
                openrouter_api_key=None,
                model_gateway_routes="[]",
            )
        )

    planning_only = json.dumps([_custom_gateway_route(capability="planning")])
    with pytest.raises(RuntimeError, match="missing capabilities: transcription"):
        _validate_production_settings(
            _production_settings(
                groq_api_key=None,
                openrouter_api_key=None,
                model_gateway_routes=planning_only,
                model_gateway_openai_base_url="https://models.example.com/v1",
                model_gateway_openai_api_key="custom-key",
            )
        )


def test_production_gateway_rejects_disabled_floating_and_plaintext_routes() -> None:
    with pytest.raises(RuntimeError, match="MODEL_GATEWAY_ENABLED"):
        _validate_production_settings(_production_settings(model_gateway_enabled=False))

    routes = [
        _custom_gateway_route(capability="planning", revision="model@latest"),
        _custom_gateway_route(capability="transcription", revision="model-v2"),
    ]
    with pytest.raises(RuntimeError, match="pinned"):
        _validate_production_settings(
            _production_settings(
                groq_api_key=None,
                openrouter_api_key=None,
                model_gateway_routes=json.dumps(routes),
                model_gateway_openai_base_url="https://models.example.com/v1",
                model_gateway_openai_api_key="custom-key",
            )
        )

    pinned_routes = [
        _custom_gateway_route(capability="planning"),
        _custom_gateway_route(capability="transcription"),
    ]
    with pytest.raises(RuntimeError, match="HTTPS"):
        _validate_production_settings(
            _production_settings(
                groq_api_key=None,
                openrouter_api_key=None,
                model_gateway_routes=json.dumps(pinned_routes),
                model_gateway_openai_base_url="http://models.internal/v1",
                model_gateway_openai_api_key="custom-key",
            )
        )


def test_constant_time_token_comparison_uses_compare_digest(monkeypatch) -> None:
    calls: list[tuple[bytes, bytes]] = []

    def spy(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr("app.core.security.compare_digest", spy)

    assert constant_time_token_matches("worker-token", "worker-token") is True
    assert calls == [(b"worker-token", b"worker-token")]


def test_internal_auth_accepts_worker_token_and_rejects_admin_token() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    worker_api_token="worker-only-token",
                    admin_api_token="admin-only-token",
                )
            )
        )
    )

    asyncio.run(require_worker_token(request, "worker-only-token"))
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(require_worker_token(request, "admin-only-token"))
    assert exc_info.value.status_code == 403


def test_production_api_requires_worker_secret_but_not_admin_token() -> None:
    _validate_production_settings(_production_settings(admin_api_token=None))

    with pytest.raises(RuntimeError, match="WORKER_API_TOKEN"):
        _validate_production_settings(_production_settings(worker_api_token=None))


def test_production_worker_requires_worker_token_without_admin_secret() -> None:
    settings = _production_settings(
        process_role="worker",
        worker_api_token="worker-only-token",
        admin_api_token=None,
    )

    _validate_production_settings(settings)
    with pytest.raises(RuntimeError, match="WORKER_API_TOKEN"):
        _validate_production_settings(settings.model_copy(update={"worker_api_token": None}))


def test_production_requires_an_explicit_billing_mode() -> None:
    """B17: production must state its billing posture; there is no default."""
    with pytest.raises(RuntimeError, match="BILLING_MODE is required"):
        _validate_production_settings(_production_settings(billing_mode=""))

    with pytest.raises(RuntimeError, match="unsupported production value"):
        _validate_production_settings(_production_settings(billing_mode="stripe"))

    # Both supported values start cleanly.
    _validate_production_settings(_production_settings(billing_mode="disabled"))
    _validate_production_settings(_production_settings(billing_mode="demo"))


def test_api_security_headers_are_applied_at_runtime() -> None:
    app = FastAPI()
    app.middleware("http")(security_headers_middleware)

    @app.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/probe")

    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "camera=()" in response.headers["permissions-policy"]


def test_worker_credentials_are_structurally_separated_in_runtime_config() -> None:
    # Task 32 removed the dead /internal/jobs run route and all /internal/renders
    # routes. internal_jobs.py now holds only the shared require_worker_token
    # dependency (no APIRouter); the in-process job engine moved to the non-route
    # module app/services/job_engine.py. The remaining internal control-plane
    # surface is batches/retention/workflows/operations.
    internal_route_paths = list((ROOT / "apps/api/app/api").glob("internal_*.py"))
    internal_route_paths.append(ROOT / "apps/api/app/api/operations.py")
    internal_route_sources = {
        path.name: path.read_text(encoding="utf-8") for path in internal_route_paths
    }
    assert set(internal_route_sources) == {
        "internal_batches.py",
        "internal_jobs.py",
        "internal_retention.py",
        "internal_workflows.py",
        "operations.py",
    }
    # The dead HTTP surfaces are gone; the job engine is a non-route module.
    assert not (ROOT / "apps/api/app/api/internal_renders.py").exists()
    assert "APIRouter" not in internal_route_sources["internal_jobs.py"]
    for relative, source in internal_route_sources.items():
        assert "require_worker_token" in source, relative
        assert "admin_api_token" not in source, relative

    worker_auth_sources = [
        source
        for source in internal_route_sources.values()
        if "async def require_worker_token" in source or "async def _require_worker_token" in source
    ]
    assert worker_auth_sources
    for source in worker_auth_sources:
        assert "worker_api_token" in source
        assert "constant_time_token_matches" in source

    admin_source = (ROOT / "apps/api/app/api/admin.py").read_text(encoding="utf-8")
    assert "require_admin" in admin_source
    assert "admin_api_token" in admin_source
    assert "constant_time_token_matches" in admin_source

    # The worker queue bridge is a single source (apps/worker/src); the API
    # control-plane copy is produced by the Dockerfiles at build time (Task 32).
    source = (ROOT / "apps/worker/src/queue-common.mjs").read_text(encoding="utf-8")
    assert 'return env("WORKER_API_TOKEN");' in source
    assert "ADMIN_API_TOKEN" not in source
    assert not (ROOT / "apps/api/worker-bridge/queue-common.mjs").exists()
    for dockerfile in ("Dockerfile", "apps/api/Dockerfile"):
        text = (ROOT / dockerfile).read_text(encoding="utf-8")
        assert (
            "COPY apps/worker/src/queue-common.mjs ./apps/api/worker-bridge/queue-common.mjs"
            in text
        )

    compose = (ROOT / "infra/docker-compose.gpu.yml").read_text(encoding="utf-8")
    shared_worker_env = compose.split("x-media-environment:", 1)[1].split("x-media-volumes:", 1)[0]
    assert "WORKER_API_TOKEN_FILE: /run/secrets/worker_api_token" in shared_worker_env
    assert "ADMIN_API_TOKEN" not in shared_worker_env


def test_registered_internal_and_admin_routes_remain_separately_authorized() -> None:
    import inspect

    from app.api.admin import require_admin
    from app.api.internal_jobs import require_worker_token
    from app.main import app

    internal_routes = []
    admin_routes = []
    for route in app.routes:
        if hasattr(route, "path") and route.path.startswith("/internal"):
            internal_routes.append(route)
        elif hasattr(route, "path") and route.path.startswith("/admin"):
            admin_routes.append(route)
        elif hasattr(route, "original_router"):
            for subroute in route.original_router.routes:
                if hasattr(subroute, "path") and subroute.path.startswith("/internal"):
                    internal_routes.append(subroute)
                elif hasattr(subroute, "path") and subroute.path.startswith("/admin"):
                    admin_routes.append(subroute)

    assert internal_routes
    assert {
        "/internal/operations/heartbeat",
        "/internal/operations/provider-slo",
        "/internal/operations/gpu-autoscaling/run",
    }.issubset({route.path for route in internal_routes})
    for route in internal_routes:
        endpoint_source = inspect.getsource(route.endpoint)
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert require_worker_token in dependencies or "require_worker_token" in endpoint_source, (
            f"{route.path} was not protected"
        )
        assert "require_admin" not in endpoint_source, route.path

    assert admin_routes
    for route in admin_routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert require_admin in dependencies, f"{route.path} was not admin-authorized"


def test_runtime_containers_drop_root_and_own_writable_paths() -> None:
    dockerfiles = (
        "apps/web/Dockerfile",
        "apps/api/Dockerfile",
        "apps/worker/Dockerfile",
        "apps/worker/Dockerfile.python",
        "apps/worker/Dockerfile.python.demucs",
        "apps/worker/Dockerfile.gpu",
        "apps/api/Dockerfile.gpu",
    )
    for relative in dockerfiles:
        source = (ROOT / relative).read_text(encoding="utf-8")
        users = re.findall(r"^USER\s+(\S+)", source, flags=re.MULTILINE)
        assert users and users[-1].lower() not in {"0", "root"}, relative
        assert "chown" in source, relative

    for relative in dockerfiles[1:]:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "--uid 10001" in source, f"{relative} must use the shared runtime UID"
        for path in ("/var/cache/motionly", "/var/data/motionly", "/tmp/motionly"):
            assert path in source, f"{relative} does not provision {path}"


def test_runtime_user_owns_the_home_directory_its_image_declares() -> None:
    """Root build steps (npm, pnpm, pip) run with HOME set and create the home
    directory before ``useradd --create-home`` runs, which then skips it. A
    root-owned home stops Chromium creating its profile and crash database, so
    every Remotion render fails. Each image that sets HOME must chown it."""
    for path in sorted((ROOT / "apps").glob("*/Dockerfile*")):
        source = path.read_text(encoding="utf-8")
        declared = re.search(r"\bHOME=(/home/[A-Za-z0-9_-]+)", source)
        if not declared:
            continue
        home = declared.group(1)
        owners = re.findall(r"chown -R \S+((?:\s+(?:\\\n\s*)?/[^\s\\]+)+)", source)
        owned_paths = {item for group in owners for item in group.split() if item.startswith("/")}
        assert home in owned_paths, f"{path.relative_to(ROOT)} must chown its HOME {home}"


def test_gpu_compose_initializes_shared_volume_ownership_without_worker_admin_secrets() -> None:
    compose = (ROOT / "infra/docker-compose.gpu.yml").read_text(encoding="utf-8")
    initializer = re.search(r"(?ms)^  volume-permissions:\n(.*?)(?=^  postgres:\n)", compose)
    assert initializer is not None
    initializer_source = initializer.group(1)
    assert "image: alpine:3.21.3" in initializer_source
    assert "network_mode: none" in initializer_source
    assert 'restart: "no"' in initializer_source
    assert 'user: "0:0"' in initializer_source
    assert "read_only: true" in initializer_source
    assert "chown -R 10001:10001" in initializer_source
    assert "/var/data/motionly" in initializer_source
    assert "/var/cache/motionly" in initializer_source

    worker_dependencies = compose.split("x-worker-depends-on:", 1)[1].split(
        "x-media-environment:", 1
    )[0]
    assert "api: { condition: service_started }" in worker_dependencies
    assert (
        "volume-permissions: { condition: service_completed_successfully }" in worker_dependencies
    )

    api_service = re.search(r"(?ms)^  api:\n(.*?)(?=^  web:\n)", compose)
    assert api_service is not None
    assert "volume-permissions: { condition: service_completed_successfully }" in (
        api_service.group(1)
    )

    worker_services = (
        "cpu-ffmpeg-worker",
        "gpu-inference-worker",
        "matte-worker",
        "gpu-ffmpeg-worker",
        "demucs-worker",
        "provider-worker",
        "planning-worker",
        "remotion-worker",
    )
    for service in worker_services:
        service_block = re.search(
            rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-z0-9-]+:\n|\Z)",
            compose,
        )
        assert service_block is not None, service
        source = service_block.group(1)
        assert "depends_on: *worker-depends-on" in source, service
        assert "admin_api_token" not in source, service

    worker_secrets_match = re.search(r"(?ms)&worker-secrets\s*\[(.*?)\]", compose)
    assert worker_secrets_match is not None
    worker_secrets = worker_secrets_match.group(1)
    assert "admin_api_token" not in worker_secrets


def test_environment_templates_contain_only_distinct_placeholders() -> None:
    root_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    api_example = (ROOT / "apps/api/.env.example").read_text(encoding="utf-8")
    worker_example = (ROOT / "apps/worker/.env.example").read_text(encoding="utf-8")
    production_template = (ROOT / "prod.env.example").read_text(encoding="utf-8")

    assert "ADMIN_API_TOKEN=replace-with-distinct-admin-token" in root_example
    assert "WORKER_API_TOKEN=replace-with-distinct-worker-token" in root_example
    assert "WORKER_API_TOKEN=replace-with-distinct-worker-token" in api_example
    assert "WORKER_API_TOKEN=replace-with-distinct-worker-token" in worker_example
    assert "ADMIN_API_TOKEN" not in production_template
    for line in production_template.splitlines():
        if line.startswith("export ") and '="' in line:
            name = line.removeprefix("export ").split("=", 1)[0]
            value = line.split('="', 1)[1].removesuffix('"')
            if name.endswith("_POLICY_VERSION"):
                assert re.fullmatch(r"[a-z][a-z0-9-]*/v[1-9]\d*", value), name
                continue
            allowed_literals = {
                "production",
                "worker",
                "true",
                "false",
                "1",
                "visual_analysis",
                "exports",
                "raw-uploads",
                "groq",
                "openrouter",
                "local_ml",
                "demucs",
                "licensed_catalog",
                "pexels_pixabay",
                "gen3a_turbo",
                "openai/gpt-4.1-mini",
                "whisper-large-v3-turbo",
                "your-namespace.tmprl.cloud:7233",
                "your-namespace.your-account",
                "motionly-workflows",
                "900000",
                "120000",
                "30000",
            }
            assert (
                value in allowed_literals
                or "replace-with" in value
                or value.startswith("https://your-")
                or value.startswith("redis://default:replace-with")
                or re.fullmatch(r"\d+(?:\.\d+)?", value)
                or re.fullmatch(r"[0-9a-f]{40,64}", value)
                or re.fullmatch(r"[A-Za-z0-9._/-]+-\d{4}-\d{2}-\d{2}", value)
                or re.fullmatch(r"[A-Za-z0-9._-]+-v?\d+(?:\.\d+)*", value)
                or re.fullmatch(r"[A-Za-z0-9._-]+==\d+(?:\.\d+)*", value)
                or value.startswith("https://github.com/ultralytics/assets/releases/download/")
            )


def test_production_gateway_requires_explicit_external_and_runway_pricing() -> None:
    unpriced = _custom_gateway_route(capability="planning")
    unpriced.pop("fixed_cost_usd")
    routes = [unpriced, _custom_gateway_route(capability="transcription")]
    with pytest.raises(RuntimeError, match="explicit pricing"):
        _validate_production_settings(
            _production_settings(
                groq_api_key=None,
                openrouter_api_key=None,
                model_gateway_routes=json.dumps(routes),
                model_gateway_openai_base_url="https://models.example.com/v1",
                model_gateway_openai_api_key="custom-key",
            )
        )

    with pytest.raises(RuntimeError, match="RUNWAY_ESTIMATED_COST_PER_REQUEST_USD"):
        _validate_production_settings(_production_settings(runway_estimated_cost_per_request_usd=0))


def test_production_planning_route_cannot_bypass_budget_as_explicitly_free() -> None:
    routes = [
        {
            **_custom_gateway_route(capability="planning"),
            "fixed_cost_usd": 0,
            "explicitly_free": True,
        },
        _custom_gateway_route(capability="transcription"),
    ]
    with pytest.raises(RuntimeError, match="planning routes cannot be explicitly free"):
        _validate_production_settings(
            _production_settings(
                groq_api_key=None,
                openrouter_api_key=None,
                model_gateway_routes=json.dumps(routes),
                model_gateway_openai_base_url="https://models.example.com/v1",
                model_gateway_openai_api_key="custom-key",
            )
        )


@pytest.mark.parametrize(
    ("module_name", "entrypoint"),
    [
        ("app.workers.visual_job", "_runner"),
        ("app.workers.matte_job", "_runner"),
        ("app.workers.prefetch_models", "main"),
    ],
)
def test_standalone_worker_entrypoints_validate_production_before_initialization(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    entrypoint: str,
) -> None:
    import importlib

    module = importlib.import_module(module_name)
    settings = SimpleNamespace(app_env="production")
    monkeypatch.setattr(module, "get_settings", lambda: settings)

    def reject(value):
        assert value is settings
        raise RuntimeError("production-validation-sentinel")

    monkeypatch.setattr(module, "validate_production_settings", reject)
    with pytest.raises(RuntimeError, match="production-validation-sentinel"):
        getattr(module, entrypoint)()


def test_task10_deployment_templates_expose_complete_gateway_contract() -> None:
    api_example = (ROOT / "apps/api/.env.example").read_text(encoding="utf-8")
    production_template = (ROOT / "prod.env.example").read_text(encoding="utf-8")
    compose = (ROOT / "infra/docker-compose.gpu.yml").read_text(encoding="utf-8")
    required = {
        "MODEL_GATEWAY_ENABLED",
        "MODEL_GATEWAY_MONTHLY_BUDGET_USD",
        "MODEL_GATEWAY_REQUEST_BUDGET_USD",
        "TRANSCRIPTION_MODEL_REVISION",
        "EDIT_PLANNER_MODEL_REVISION",
        "AUDIO_ML_PROVIDER",
        "VISUAL_CLIP_MODEL_REVISION",
        "RUNWAY_API_KEY",
        "RUNWAY_MODEL_REVISION",
        "RUNWAY_ESTIMATED_COST_PER_REQUEST_USD",
    }
    for name in required:
        assert name in api_example, name
        assert name in production_template, name
        assert name in compose, name
    assert "AZURE_OPENAI_ENDPOINT" in api_example
    assert "AZURE_OPENAI_API_KEY" in api_example
    assert "openrouter/free" not in api_example
    assert "openrouter/free" not in production_template


def _settings_from_environment_template(path: Path):
    from app.core.config import Settings

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ")
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip().strip('"')
        if name.endswith("_POLICY_VERSION"):
            assert re.fullmatch(r"[a-z][a-z0-9-]*/v[1-9]\d*", value), name
        if value:
            values[name] = value
    return Settings(_env_file=None, **values)


def test_documented_production_api_and_worker_environments_pass_startup_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as main_module
    from app.services.model_gateway import ModelCapability

    monkeypatch.setenv("SKIP_API_AUTH", "false")
    api_settings = _settings_from_environment_template(ROOT / ".env.example")
    worker_settings = _settings_from_environment_template(ROOT / "prod.env.example")

    assert api_settings.process_role == "api"
    assert worker_settings.process_role == "worker"
    _validate_production_settings(api_settings)
    _validate_production_settings(worker_settings)

    monkeypatch.setattr(main_module, "get_settings", lambda: api_settings)
    api_app = main_module.create_app()
    api_capabilities = {route.capability for route in api_app.state.model_gateway.routes}
    assert api_capabilities == {
        ModelCapability.PLANNING,
        ModelCapability.TRANSCRIPTION,
        ModelCapability.EMBEDDINGS,
        ModelCapability.GENERATION,
    }
    assert api_app.state.runway_gateway_route.fixed_cost_usd == 0.5
    assert api_app.state.stem_gateway_route is None
    assert isinstance(api_app.state.stock_asset_enrichment_queue, StockAssetEnrichmentQueue)
    assert api_app.state.stock_asset_enrichment_queue.registry is api_app.state.queue_registry

    monkeypatch.setattr(main_module, "get_settings", lambda: worker_settings)
    worker_app = main_module.create_app()
    worker_capabilities = {route.capability for route in worker_app.state.model_gateway.routes}
    assert worker_capabilities == api_capabilities | {ModelCapability.AUDIO}
    assert worker_app.state.audio_intelligence_provider is not None
    assert worker_app.state.stem_gateway_route.model_revision == "demucs==4.1.0"
    assert worker_app.state.runway_gateway_route.fixed_cost_usd == 0.5


@pytest.mark.parametrize(
    ("process_role", "redis_url"),
    [
        ("temporal-dispatcher", None),
        ("temporal-worker", "redis://redis.example.com:6379"),
        ("workflow-reconciler", None),
    ],
)
def test_production_orchestration_roles_use_only_role_specific_dependencies(
    process_role: str,
    redis_url: str | None,
) -> None:
    settings = _production_settings(
        process_role=process_role,
        temporal_address="namespace.tmprl.cloud:7233",
        temporal_namespace="namespace.account",
        temporal_api_key="temporal-key",
        temporal_task_queue="motionly-workflows",
        temporal_tls_enabled=True,
        redis_url=redis_url,
        groq_api_key=None,
        openrouter_api_key=None,
        runway_api_key=None,
        pexels_api_key=None,
        pixabay_api_key=None,
        worker_api_base_url=(
            "https://api.example.com" if process_role == "temporal-worker" else None
        ),
        worker_api_token=("worker-secret" if process_role == "temporal-worker" else None),
        metrics_bearer_token=None,
        model_gateway_enabled=False,
    )

    _validate_production_settings(settings)


@pytest.mark.parametrize(
    ("process_role", "redis_url"),
    [
        ("temporal-dispatcher", None),
        ("temporal-worker", "redis://redis.example.com:6379"),
        ("workflow-reconciler", None),
    ],
)
def test_self_hosted_temporal_roles_allow_private_compose_without_api_key(
    process_role: str,
    redis_url: str | None,
) -> None:
    settings = _production_settings(
        process_role=process_role,
        temporal_deployment_mode="self_hosted",
        temporal_address="temporal:7233",
        temporal_namespace="motionly",
        temporal_api_key=None,
        temporal_task_queue="motionly-orchestration",
        temporal_tls_enabled=True,
        temporal_server_root_ca_path="/run/secrets/temporal_ca_cert",
        temporal_client_cert_path="/run/secrets/temporal_client_cert",
        temporal_client_private_key_path="/run/secrets/temporal_client_key",
        temporal_server_name="temporal",
        temporal_client_role={
            "temporal-dispatcher": "dispatcher",
            "temporal-worker": "worker",
            "workflow-reconciler": "reconciler",
        }[process_role],
        temporal_identity={
            "temporal-dispatcher": "motionly-dispatcher",
            "temporal-worker": "motionly-temporal-worker",
            "workflow-reconciler": "motionly-reconciler",
        }[process_role],
        redis_url=redis_url,
        groq_api_key=None,
        openrouter_api_key=None,
        runway_api_key=None,
        pexels_api_key=None,
        pixabay_api_key=None,
        worker_api_base_url=("http://api:8000" if process_role == "temporal-worker" else None),
        worker_api_token=("worker-secret" if process_role == "temporal-worker" else None),
        metrics_bearer_token=None,
        model_gateway_enabled=False,
    )

    _validate_production_settings(settings)


def test_self_hosted_temporal_rejects_non_private_or_incomplete_configuration() -> None:
    settings = _production_settings(
        process_role="temporal-dispatcher",
        temporal_deployment_mode="self_hosted",
        temporal_address="temporal:7233",
        temporal_namespace="motionly",
        temporal_api_key=None,
        temporal_task_queue="motionly-orchestration",
        temporal_tls_enabled=True,
        temporal_server_root_ca_path="/run/secrets/temporal_ca_cert",
        temporal_client_cert_path="/run/secrets/temporal_dispatcher_cert",
        temporal_client_private_key_path="/run/secrets/temporal_dispatcher_key",
        temporal_server_name="temporal",
        temporal_client_role="dispatcher",
        temporal_identity="motionly-dispatcher",
    )

    with pytest.raises(RuntimeError, match="private Docker service"):
        _validate_production_settings(
            settings.model_copy(update={"temporal_address": "temporal.example.test:7233"})
        )
    with pytest.raises(RuntimeError, match="TEMPORAL_TASK_QUEUE"):
        _validate_production_settings(settings.model_copy(update={"temporal_task_queue": None}))


def test_self_hosted_temporal_rejects_missing_mtls_or_role_safe_identity() -> None:
    settings = _production_settings(
        process_role="temporal-dispatcher",
        temporal_deployment_mode="self_hosted",
        temporal_address="temporal:7233",
        temporal_namespace="motionly",
        temporal_task_queue="motionly-orchestration",
        temporal_tls_enabled=True,
        temporal_server_root_ca_path="/run/secrets/temporal_ca_cert",
        temporal_client_cert_path="/run/secrets/temporal_dispatcher_cert",
        temporal_client_private_key_path="/run/secrets/temporal_dispatcher_key",
        temporal_server_name="temporal",
        temporal_client_role="dispatcher",
        temporal_identity="motionly-dispatcher",
    )
    for field_name, expected in (
        ("temporal_server_root_ca_path", "TEMPORAL_SERVER_ROOT_CA_PATH"),
        ("temporal_client_cert_path", "TEMPORAL_CLIENT_CERT_PATH"),
        ("temporal_client_private_key_path", "TEMPORAL_CLIENT_PRIVATE_KEY_PATH"),
        ("temporal_server_name", "TEMPORAL_SERVER_NAME"),
        ("temporal_client_role", "TEMPORAL_CLIENT_ROLE"),
        ("temporal_identity", "TEMPORAL_IDENTITY"),
    ):
        with pytest.raises(RuntimeError, match=expected):
            _validate_production_settings(settings.model_copy(update={field_name: None}))
    with pytest.raises(RuntimeError, match="TEMPORAL_CLIENT_ROLE"):
        _validate_production_settings(settings.model_copy(update={"temporal_client_role": "api"}))
    with pytest.raises(RuntimeError, match="TEMPORAL_IDENTITY"):
        _validate_production_settings(
            settings.model_copy(update={"temporal_identity": "motionly-api"})
        )


@pytest.mark.parametrize(
    "process_role",
    ["temporal-dispatcher", "temporal-worker", "workflow-reconciler"],
)
@pytest.mark.parametrize(
    ("field_name", "environment_name"),
    [
        ("temporal_address", "TEMPORAL_ADDRESS"),
        ("temporal_namespace", "TEMPORAL_NAMESPACE"),
        ("temporal_api_key", "TEMPORAL_API_KEY"),
        ("temporal_task_queue", "TEMPORAL_TASK_QUEUE"),
    ],
)
def test_production_orchestration_roles_fail_closed_without_temporal_settings(
    process_role: str,
    field_name: str,
    environment_name: str,
) -> None:
    settings = _production_settings(
        process_role=process_role,
        temporal_address="namespace.tmprl.cloud:7233",
        temporal_namespace="namespace.account",
        temporal_api_key="temporal-key",
        temporal_task_queue="motionly-workflows",
    ).model_copy(update={field_name: None})

    with pytest.raises(RuntimeError, match=environment_name):
        _validate_production_settings(settings)


def test_production_orchestration_roles_require_tls_and_canonical_supabase() -> None:
    settings = _production_settings(
        process_role="temporal-dispatcher",
        temporal_address="namespace.tmprl.cloud:7233",
        temporal_namespace="namespace.account",
        temporal_api_key="temporal-key",
        temporal_task_queue="motionly-workflows",
    )

    with pytest.raises(RuntimeError, match="TEMPORAL_TLS_ENABLED"):
        _validate_production_settings(settings.model_copy(update={"temporal_tls_enabled": False}))
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        _validate_production_settings(settings.model_copy(update={"supabase_url": None}))
    with pytest.raises(RuntimeError, match="SUPABASE_JWT_SECRET"):
        _validate_production_settings(settings.model_copy(update={"supabase_jwt_secret": None}))
    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        _validate_production_settings(
            settings.model_copy(update={"supabase_service_role_key": None})
        )


@pytest.mark.parametrize("redis_url", [None, "   "])
def test_production_temporal_worker_alone_requires_redis(redis_url: str | None) -> None:
    settings = _production_settings(
        process_role="temporal-worker",
        temporal_address="namespace.tmprl.cloud:7233",
        temporal_namespace="namespace.account",
        temporal_api_key="temporal-key",
        temporal_task_queue="motionly-workflows",
        redis_url=redis_url,
    )

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        _validate_production_settings(settings)


@pytest.mark.parametrize(
    ("field_name", "environment_name"),
    [
        ("worker_api_base_url", "WORKER_API_BASE_URL"),
        ("worker_api_token", "WORKER_API_TOKEN"),
    ],
)
def test_production_temporal_worker_requires_proposal_activity_transport(
    field_name: str, environment_name: str
) -> None:
    settings = _production_settings(
        process_role="temporal-worker",
        temporal_address="namespace.tmprl.cloud:7233",
        temporal_namespace="namespace.account",
        temporal_api_key="temporal-key",
        temporal_task_queue="motionly-workflows",
        temporal_tls_enabled=True,
        redis_url="redis://redis.example.com:6379",
        worker_api_base_url="https://api.example.com",
        worker_api_token="worker-secret",
    ).model_copy(update={field_name: None})

    with pytest.raises(RuntimeError, match=environment_name):
        _validate_production_settings(settings)


def test_production_api_and_media_worker_do_not_require_temporal_settings() -> None:
    _validate_production_settings(_production_settings(process_role="api"))
    _validate_production_settings(_production_settings(process_role="worker"))
