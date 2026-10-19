from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_api_image_contains_no_ffmpeg_or_ml_runtime() -> None:
    dockerfiles = [
        ROOT / "Dockerfile",
        ROOT / "apps/api/Dockerfile",
    ]
    pyproject = (ROOT / "apps/api/pyproject.toml").read_text()
    core_dependencies = pyproject.split("[project.optional-dependencies]", 1)[0]
    for dockerfile in dockerfiles:
        dockerfile_lower = dockerfile.read_text().lower()
        assert "ffmpeg" not in dockerfile_lower
        assert "apps/api/worker-bridge" in dockerfile_lower
        # The Node worker bridge needs a Node runtime: Node 24 copied from the
        # pinned official image (Decision D1), not Debian's Node 20 package.
        assert "copy --from=node:24-bookworm-slim@sha256:" in dockerfile_lower
        assert "/usr/local/bin/node /usr/local/bin/node" in dockerfile_lower
        assert "worker-bridge/package.json" in dockerfile_lower
        assert "worker-bridge/package-lock.json" in dockerfile_lower
        assert "npm ci --omit=dev" in dockerfile_lower
    # The npm island manifest must be committed (not gitignored) or `npm ci`
    # fails in both Docker builds and the supply-chain CI job.
    worker_bridge_manifest = ROOT / "apps/api/worker-bridge/package.json"
    assert worker_bridge_manifest.exists(), (
        "apps/api/worker-bridge/package.json is required by the API Dockerfiles "
        "and supply-chain CI (`npm ci`); it must be committed."
    )
    manifest_text = worker_bridge_manifest.read_text()
    assert '"bullmq": "5.78.0"' in manifest_text
    assert '"ioredis": "5.11.1"' in manifest_text
    worker_bridge_lock = (
        ROOT / "apps/api/worker-bridge/package-lock.json"
    ).read_text()
    assert '"bullmq": "5.78.0"' in worker_bridge_lock
    for dependency in ("torch", "tensorflow", "transformers", "demucs", "pyannote"):
        assert dependency not in core_dependencies.lower()


def test_media_runtime_stays_in_worker_images() -> None:
    worker_dockerfile = (ROOT / "apps/worker/Dockerfile").read_text().lower()
    python_worker_dockerfile = (ROOT / "apps/worker/Dockerfile.python").read_text().lower()
    assert "ffmpeg" in worker_dockerfile
    assert "chromium" in worker_dockerfile
    assert "ffmpeg" in python_worker_dockerfile


def test_api_image_uses_configurable_process_count() -> None:
    for dockerfile in (ROOT / "Dockerfile", ROOT / "apps/api/Dockerfile"):
        source = dockerfile.read_text()
        assert "WEB_CONCURRENCY=2" in source
        assert "--workers ${WEB_CONCURRENCY:-2}" in source


def test_workers_never_route_domain_compute_through_internal_api() -> None:
    worker = (ROOT / "apps/worker/src/worker.mjs").read_text()
    remotion = (ROOT / "apps/worker/src/remotion.mjs").read_text()
    assert "/internal/jobs" not in worker
    assert "/internal/renders" not in remotion
    assert "loadRenderJobDirect" in remotion
    assert "persistCompleteDirect" in remotion


def test_heavy_endpoints_enqueue_worker_jobs() -> None:
    api_dir = ROOT / "apps/api/app/api"
    required = {
        "plans.py": "enqueue_plan(",
        "broll.py": "enqueue_broll(",
        "music.py": "enqueue_music_selection(",
        "effects.py": "enqueue_effects_config(",
        "captions.py": "enqueue_caption_generation(",
    }
    for filename, call in required.items():
        assert call in (api_dir / filename).read_text()


def test_heavy_endpoints_require_worker_queue_before_enqueue() -> None:
    api_dir = ROOT / "apps/api/app/api"
    for filename in (
        "plans.py",
        "broll.py",
        "music.py",
        "effects.py",
        "captions.py",
    ):
        source = (api_dir / filename).read_text()
        assert "prepare_heavy_job_enqueue" in source


def test_specialized_worker_pools_and_persistent_model_cache_are_declared() -> None:
    compose = (ROOT / "infra/docker-compose.gpu.yml").read_text()
    for service in (
        "cpu-ffmpeg-worker",
        "gpu-inference-worker",
        "demucs-worker",
        "provider-worker",
        "remotion-worker",
    ):
        assert service in compose
    assert "motionly-model-cache" in compose
    assert "/var/cache/motionly" in compose


def test_contract_generation_has_no_runtime_database_migration() -> None:
    generator = (ROOT / "apps/api/scripts/generate_openapi_contract.py").read_text()

    assert 'os.environ["SETTINGS_ENV_FILE"] = "none"' in generator
