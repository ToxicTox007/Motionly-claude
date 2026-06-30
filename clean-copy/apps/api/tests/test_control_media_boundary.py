from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_api_image_contains_no_ffmpeg_or_ml_runtime() -> None:
    dockerfile = (ROOT / "apps/api/Dockerfile").read_text()
    pyproject = (ROOT / "apps/api/pyproject.toml").read_text()
    core_dependencies = pyproject.split("[project.optional-dependencies]", 1)[0]
    assert "ffmpeg" not in dockerfile.lower()
    for dependency in ("torch", "tensorflow", "transformers", "demucs", "pyannote"):
        assert dependency not in core_dependencies.lower()


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
    for filename in ("plans.py", "broll.py", "music.py", "effects.py", "captions.py", "publishing.py"):
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
        "publishing-worker",
    ):
        assert service in compose
    assert "motionly-model-cache" in compose
    assert "/var/cache/motionly" in compose
