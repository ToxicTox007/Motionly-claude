"""GPU-free cpu-vision worker image and CPU YAMNet (AUDIO_SOUND_PROVIDER=yamnet_tflite)."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import sys
import types
import wave
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.main import create_app
from app.schemas.captions import TranscriptWord
from app.schemas.scale import SoundClassification
from app.services.audio_intelligence import merge_audio_intelligence
from app.services.audio_ml_providers import YamnetTFLiteSoundClassifier
from app.services.model_domain_adapters import bundle_revision

ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "apps" / "api"
CPU_LOCK = API_ROOT / "requirements-cpu-vision.lock"
DOCKERFILE = ROOT / "apps" / "worker" / "Dockerfile.python"
YAMNET_SHA256 = "4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf"
PACKAGE_LINE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", re.MULTILINE)


def _locked(path: Path) -> dict[str, str]:
    return {name.lower(): version for name, version in PACKAGE_LINE.findall(path.read_text())}


def _wav(tmp_path: Path, *, seconds: float = 2.0, width: int = 2, channels: int = 1) -> Path:
    path = tmp_path / "speech.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(16_000)
        audio.writeframes(b"\x10\x00" * int(16_000 * seconds) * channels)
    return path


def _model(tmp_path: Path, content: bytes = b"fake-tflite-model") -> tuple[Path, str]:
    path = tmp_path / "yamnet.tflite"
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


# -- fake MediaPipe audio classifier ------------------------------------------------


class _Category:
    def __init__(self, name: str, score: float, index: int) -> None:
        self.category_name = name
        self.score = score
        self.index = index


class _Result:
    def __init__(self, scores: dict[str, float]) -> None:
        categories = [
            _Category(name, score, index) for index, (name, score) in enumerate(scores.items())
        ]
        self.classifications = [types.SimpleNamespace(categories=categories)]


def _install_fake_mediapipe(monkeypatch: pytest.MonkeyPatch, windows: list[dict[str, float]]):
    seen: dict[str, object] = {}

    class FakeClassifier:
        @classmethod
        def create_from_options(cls, options):
            seen["options"] = options
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def classify(self, data):
            seen["data"] = data
            return [_Result(scores) for scores in windows]

    audio = types.SimpleNamespace(
        AudioClassifier=FakeClassifier,
        AudioClassifierOptions=lambda **kwargs: kwargs,
    )
    audio_data = types.SimpleNamespace(
        AudioData=types.SimpleNamespace(
            create_from_array=lambda samples, rate: {"samples": samples, "rate": rate}
        )
    )
    base_options = types.SimpleNamespace(BaseOptions=lambda **kwargs: kwargs)
    modules = {
        "mediapipe": types.ModuleType("mediapipe"),
        "mediapipe.tasks": types.ModuleType("mediapipe.tasks"),
        "mediapipe.tasks.python": types.ModuleType("mediapipe.tasks.python"),
        "mediapipe.tasks.python.components": types.ModuleType("components"),
        "mediapipe.tasks.python.components.containers": types.ModuleType("containers"),
        "mediapipe.tasks.python.core": types.ModuleType("core"),
    }
    modules["mediapipe.tasks.python"].audio = audio
    modules["mediapipe.tasks.python.components.containers"].audio_data = audio_data
    modules["mediapipe.tasks.python.core"].base_options = base_options
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        "app.services.audio_ml_providers._modules_available", lambda *names: True
    )
    return seen


def test_classifier_averages_windows_and_returns_named_labels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _install_fake_mediapipe(
        monkeypatch,
        [
            {"Speech": 0.9, "Music": 0.3, "Silence": 0.0, "Dog": 0.05},
            {"Speech": 0.7, "Music": 0.5, "Silence": 0.1, "Dog": 0.15},
        ],
    )
    model, digest = _model(tmp_path)
    classifier = YamnetTFLiteSoundClassifier(model_path=str(model), model_sha256=digest)

    result = classifier.classify(audio_path=_wav(tmp_path))

    assert [item["label"] for item in result["labels"][:2]] == ["Speech", "Music"]
    assert result["labels"][0]["confidence"] == pytest.approx(0.8)
    assert result["background_sound"] == "Music"
    assert result["provider"] == "yamnet_tflite"
    assert "degraded" not in result
    SoundClassification.model_validate(result)
    # Every class is scored per window; the model path is passed through.
    assert seen["options"]["max_results"] == -1
    assert seen["options"]["base_options"]["model_asset_path"] == str(model)
    assert seen["data"]["rate"] == 16_000


def test_stereo_audio_is_mixed_to_mono(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = _install_fake_mediapipe(monkeypatch, [{"Speech": 0.9}])
    model, digest = _model(tmp_path)

    YamnetTFLiteSoundClassifier(model_path=str(model), model_sha256=digest).classify(
        audio_path=_wav(tmp_path, seconds=1.0, channels=2)
    )

    assert seen["data"]["samples"].shape == (16_000,)


def test_background_sound_is_none_when_only_speech_or_quiet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_mediapipe(monkeypatch, [{"Speech": 0.95, "Music": 0.04}])
    model, digest = _model(tmp_path)

    result = YamnetTFLiteSoundClassifier(model_path=str(model), model_sha256=digest).classify(
        audio_path=_wav(tmp_path)
    )

    assert result["background_sound"] == "none"


@pytest.mark.parametrize(
    ("setup", "status"),
    [
        ("missing_model", "unavailable:model_missing"),
        ("bad_checksum", "unavailable:model_checksum_mismatch"),
        ("eight_bit_audio", "unavailable:audio_unreadable"),
        ("not_audio", "unavailable:audio_unreadable"),
    ],
)
def test_failures_degrade_and_keep_the_heuristic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, setup: str, status: str
) -> None:
    _install_fake_mediapipe(monkeypatch, [{"Speech": 0.9}])
    model, digest = _model(tmp_path)
    audio = _wav(tmp_path)
    if setup == "missing_model":
        model.unlink()
    elif setup == "bad_checksum":
        digest = "0" * 64
    elif setup == "eight_bit_audio":
        audio = _wav(tmp_path, width=1)
    elif setup == "not_audio":
        audio.write_bytes(b"not a wav file")

    result = YamnetTFLiteSoundClassifier(model_path=str(model), model_sha256=digest).classify(
        audio_path=audio
    )

    assert result["degraded"] is True
    assert result["status"] == status
    assert result["heuristic_fallback"] is True


def test_native_runtime_failure_degrades(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_mediapipe(monkeypatch, [{"Speech": 0.9}])
    model, digest = _model(tmp_path)
    classifier = YamnetTFLiteSoundClassifier(model_path=str(model), model_sha256=digest)

    def broken(*_args, **_kwargs):
        raise OSError("libGLESv2.so.2: cannot open shared object file")

    monkeypatch.setattr(classifier, "_mean_scores", broken)

    result = classifier.classify(audio_path=_wav(tmp_path))

    assert result["status"] == "unavailable:runtime_error"
    assert result["heuristic_fallback"] is True


def test_missing_mediapipe_reports_dependency_missing(tmp_path: Path) -> None:
    model, digest = _model(tmp_path)
    classifier = YamnetTFLiteSoundClassifier(model_path=str(model), model_sha256=digest)

    available, reason = classifier.check_availability()

    if importlib.util.find_spec("mediapipe") is None:
        assert (available, reason) == (False, "dependency_missing")
    else:
        assert (available, reason) == (True, "available")


def test_checksum_is_verified_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_mediapipe(monkeypatch, [{"Speech": 0.9}])
    model, digest = _model(tmp_path)
    calls = []
    real = __import__("app.services.audio_ml_providers", fromlist=["_sha256_file"])._sha256_file
    monkeypatch.setattr(
        "app.services.audio_ml_providers._sha256_file",
        lambda path: calls.append(path) or real(path),
    )
    classifier = YamnetTFLiteSoundClassifier(model_path=str(model), model_sha256=digest)

    classifier.check_availability()
    classifier.check_availability()

    assert len(calls) == 1


_WORDS = [TranscriptWord(index=0, text="Hello", start_ms=0, end_ms=400, confidence=0.9)]


def test_degraded_sound_keeps_the_heuristic_estimate() -> None:
    heuristic = merge_audio_intelligence(raw_payload={}, words=_WORDS, duration_ms=2000)

    merged = merge_audio_intelligence(
        raw_payload={},
        words=_WORDS,
        duration_ms=2000,
        ml_insights={
            "sound_classification": {
                "provider": "yamnet_tflite",
                "labels": [],
                "degraded": True,
                "status": "unavailable:model_missing",
                "heuristic_fallback": True,
            }
        },
    )

    sound = merged["audio_intelligence"]["sound_classification"]
    expected = heuristic["audio_intelligence"]["sound_classification"]
    assert sound["labels"] == expected["labels"]
    assert sound["ml_status"] == "unavailable:model_missing"
    SoundClassification.model_validate(sound)


def test_degraded_tf_hub_sound_behaviour_is_unchanged() -> None:
    local_failure = {
        "provider": "yamnet",
        "labels": [],
        "degraded": True,
        "status": "unavailable:dependency_missing",
    }

    merged = merge_audio_intelligence(
        raw_payload={},
        words=_WORDS,
        duration_ms=2000,
        ml_insights={"sound_classification": local_failure},
    )

    assert merged["audio_intelligence"]["sound_classification"] == local_failure


_BAKED_MODEL = Path(os.getenv("YAMNET_TFLITE_MODEL_PATH", "/opt/motionly/models/yamnet.tflite"))


@pytest.mark.skipif(
    importlib.util.find_spec("mediapipe") is None or not _BAKED_MODEL.is_file(),
    reason="runs inside the cpu-vision image (MediaPipe + baked YAMNet model)",
)
def test_real_yamnet_model_classifies_a_tone(tmp_path: Path) -> None:
    import math
    import struct

    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 16_000)))
                for i in range(16_000 * 3)
            )
        )
    classifier = YamnetTFLiteSoundClassifier(
        model_path=str(_BAKED_MODEL),
        model_sha256=YAMNET_SHA256,
    )

    result = classifier.classify(audio_path=path)

    assert result["labels"][0]["label"] == "Sine wave"


# -- cpu-vision lock and image -------------------------------------------------------


def test_cpu_vision_lock_is_hash_complete() -> None:
    source = CPU_LOCK.read_text()
    assert "--generate-hashes" in source
    assert "--extra=cpu-vision" in source
    starts = list(re.finditer(r"^[A-Za-z0-9_.-]+==[^\s\\]+", source, re.MULTILINE))
    assert starts
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(source)
        assert "--hash=sha256:" in source[start.start() : end], start.group(0)


def test_cpu_vision_lock_has_no_gpu_or_heavy_ml_runtime() -> None:
    versions = _locked(CPU_LOCK)
    for forbidden in (
        "torch",
        "torchaudio",
        "tensorflow",
        "tensorflow-hub",
        "transformers",
        "ultralytics",
        "demucs",
        "pyannote.audio",
        "pyannote-audio",
    ):
        assert forbidden not in versions, forbidden
    for required in ("mediapipe", "numpy", "opencv-python-headless", "pillow"):
        assert required in versions, required


def test_cpu_vision_lock_versions_match_the_gpu_ml_lock() -> None:
    cpu = _locked(CPU_LOCK)
    ml = _locked(API_ROOT / "requirements-ml.lock")
    core = _locked(API_ROOT / "requirements.lock")
    mismatched = {
        name: (version, ml.get(name) or core.get(name))
        for name, version in cpu.items()
        if (ml.get(name) or core.get(name)) not in {None, version}
    }
    assert mismatched == {}


def test_dockerfile_selects_the_cpu_vision_lock_and_bakes_a_verified_model() -> None:
    dockerfile = DOCKERFILE.read_text()

    assert "COPY apps/api/requirements-cpu-vision.lock ./requirements-cpu-vision.lock" in dockerfile
    assert '"$API_EXTRAS" = "cpu-vision"' in dockerfile
    assert "--require-hashes -r /app/requirements-cpu-vision.lock" in dockerfile
    # Other API_EXTRAS values keep installing the full ML lock, empty the core lock.
    assert 'elif [ -n "$API_EXTRAS" ]; then pip install --no-cache-dir --require-hashes' in (
        dockerfile
    )
    assert "-r /app/requirements-ml.lock" in dockerfile
    assert "-r /app/requirements.lock" in dockerfile
    assert f"YAMNET_TFLITE_SHA256={YAMNET_SHA256}" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert 'ARG CPU_VISION_ENABLED="false"' in dockerfile
    assert "CPU_VISION_ENABLED=${CPU_VISION_ENABLED}" in dockerfile
    # MediaPipe's native library links EGL/GLES; only the cpu-vision variant adds them.
    assert "libegl1 libgles2" in dockerfile
    apt_step = dockerfile.split("RUN apt-get update", 1)[1].split("\n\n", 1)[0]
    assert apt_step.index('"$API_EXTRAS" = "cpu-vision"') < apt_step.index("libegl1")


def test_supply_chain_builds_the_cpu_vision_image_and_checks_its_lock() -> None:
    import yaml

    workflow_path = ROOT / ".github" / "workflows" / "supply-chain.yml"
    workflow = workflow_path.read_text()
    jobs = yaml.safe_load(workflow)["jobs"]
    matrix = jobs["build-scan-sign"]["strategy"]["matrix"]["include"]
    entry = next(item for item in matrix if item["component"] == "cpu-vision-worker")

    assert entry["dockerfile"] == "apps/worker/Dockerfile.python"
    assert "API_EXTRAS=cpu-vision" in entry["build_args"]
    assert "CPU_VISION_ENABLED=true" in entry["build_args"]
    assert "--require-hashes -r apps/api/requirements-cpu-vision.lock --dry-run" in workflow


def test_pyproject_cpu_vision_extra_has_no_gpu_runtime() -> None:
    import tomllib

    extras = tomllib.loads((API_ROOT / "pyproject.toml").read_text())["project"][
        "optional-dependencies"
    ]
    names = {re.split(r"[<>=!~\[]", item, maxsplit=1)[0].lower() for item in extras["cpu-vision"]}
    assert names == {"mediapipe", "numpy", "opencv-python-headless", "pillow"}


# -- wiring -------------------------------------------------------------------------


def _audio_route(app):
    return next(
        route for route in app.state.model_gateway.routes
        if route.route_id == "local-audio-intelligence"
    )


def test_create_app_worker_switches_sound_to_tflite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    get_settings.cache_clear()
    default_app = create_app()
    settings = get_settings()

    monkeypatch.setenv("AUDIO_SOUND_PROVIDER", "yamnet_tflite")
    get_settings.cache_clear()
    cpu_app = create_app()

    assert type(default_app.state.audio_ml_providers["sound"]).__name__ == (
        "YamnetSoundClassifier"
    )
    sound = cpu_app.state.audio_ml_providers["sound"]
    assert isinstance(sound, YamnetTFLiteSoundClassifier)
    assert sound.model_sha256 == YAMNET_SHA256
    assert _audio_route(default_app).model_revision == bundle_revision(
        settings.pyannote_model_revision,
        settings.wav2vec_emotion_model_revision,
        settings.yamnet_model_url,
    )
    assert _audio_route(cpu_app).model_revision != _audio_route(default_app).model_revision


def test_sound_can_be_disabled_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    monkeypatch.setenv("AUDIO_SOUND_PROVIDER", "disabled")
    get_settings.cache_clear()

    app = create_app()

    assert "sound" not in app.state.audio_ml_providers
    assert "emotion" in app.state.audio_ml_providers


def test_cpu_vision_flag_defaults_off_and_reads_the_image_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CPU_VISION_ENABLED", raising=False)
    get_settings.cache_clear()
    assert get_settings().cpu_vision_enabled is False
    monkeypatch.setenv("CPU_VISION_ENABLED", "true")
    get_settings.cache_clear()
    assert get_settings().cpu_vision_enabled is True
