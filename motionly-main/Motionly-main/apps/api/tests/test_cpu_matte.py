"""Background matte on CPU (MATTE_PROVIDER=local_cpu) with MediaPipe Tasks."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.main import _validate_production_settings
from app.services.cpu_vision import (
    BLAZE_FACE_FULL_RANGE,
    SELFIE_SEGMENTER_LANDSCAPE,
    CpuVisionModelError,
    TasksSelfieSegmentation,
    load_cpu_matte_models,
    verify_model_file,
)
from tests.test_service_hardening import _production_settings

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")


class _FakeMask:
    def __init__(self, array) -> None:
        self._array = array

    def numpy_view(self):
        return self._array


class _FakeSegmenter:
    def __init__(self, masks) -> None:
        self.masks = masks
        self.images: list[object] = []
        self.closed = False

    def segment(self, image):
        self.images.append(image)
        return SimpleNamespace(confidence_masks=self.masks)

    def close(self) -> None:
        self.closed = True


class _FakeMp:
    class ImageFormat:
        SRGB = "srgb"

    @staticmethod
    def Image(*, image_format, data):  # noqa: N802 - mirrors mediapipe.Image
        return {"format": image_format, "data": data}


def test_pinned_models_record_immutable_revisions() -> None:
    for model in (SELFIE_SEGMENTER_LANDSCAPE, BLAZE_FACE_FULL_RANGE):
        assert model.url.startswith("https://storage.googleapis.com/mediapipe-models/")
        assert "/latest/" not in model.url
        assert len(model.sha256) == 64
        assert model.revision == f"sha256:{model.sha256}"


def test_model_checksum_is_enforced(tmp_path: Path) -> None:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model-bytes")
    digest = hashlib.sha256(b"model-bytes").hexdigest()

    assert verify_model_file(model, digest) == model
    with pytest.raises(CpuVisionModelError, match="checksum mismatch"):
        verify_model_file(model, "0" * 64)
    with pytest.raises(CpuVisionModelError, match="missing"):
        verify_model_file(tmp_path / "absent.tflite", digest)
    with pytest.raises(CpuVisionModelError, match="checksum mismatch"):
        load_cpu_matte_models(model_path=str(model), model_sha256="0" * 64)


def test_tasks_adapter_returns_a_2d_float_mask() -> None:
    mask = np.full((4, 6, 1), 0.75, dtype=np.float32)
    segmenter = _FakeSegmenter([_FakeMask(mask)])
    adapter = TasksSelfieSegmentation(segmenter=segmenter, mp_module=_FakeMp, np_module=np)
    rgb = np.zeros((4, 6, 3), dtype=np.uint8)

    result = adapter.process(rgb)

    assert result.segmentation_mask.shape == (4, 6)
    assert result.segmentation_mask.dtype == np.float32
    assert float(result.segmentation_mask.max()) == pytest.approx(0.75)
    assert segmenter.images[0]["format"] == "srgb"
    # The returned mask is a copy, not a view into the native buffer.
    mask[...] = 0
    assert float(result.segmentation_mask.max()) == pytest.approx(0.75)


def test_tasks_adapter_handles_missing_masks() -> None:
    adapter = TasksSelfieSegmentation(
        segmenter=_FakeSegmenter([]), mp_module=_FakeMp, np_module=np
    )

    assert adapter.process(np.zeros((2, 2, 3), dtype=np.uint8)).segmentation_mask is None


def test_existing_matte_generator_runs_on_the_tasks_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.segmentation_matte import generate_selfie_segmentation_matte

    source = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (160, 90))
    for _ in range(4):
        writer.write(np.full((90, 160, 3), 80, dtype=np.uint8))
    writer.release()

    class HalfSegmenter(_FakeSegmenter):
        def segment(self, image):
            data = image["data"]
            mask = np.zeros(data.shape[:2] + (1,), dtype=np.float32)
            mask[:, data.shape[1] // 2 :] = 1.0
            return SimpleNamespace(confidence_masks=[_FakeMask(mask)])

    written: list[int] = []
    real_imwrite = cv2.imwrite

    def recording_imwrite(path, image):
        written.append(int(image.max()))
        return real_imwrite(path, image)

    monkeypatch.setattr(cv2, "imwrite", recording_imwrite)

    stats = generate_selfie_segmentation_matte(
        cv2=cv2,
        np=np,
        selfie_segmentation=TasksSelfieSegmentation(
            segmenter=HalfSegmenter([]), mp_module=_FakeMp, np_module=np
        ),
        source_path=source,
        output_path=tmp_path / "matte.mp4",
    )

    assert stats["frame_count"] == 4
    assert written == [255, 255, 255, 255]
    assert (tmp_path / "matte.mp4").stat().st_size > 0


def _runner_settings(**overrides) -> Settings:
    values = {
        "APP_ENV": "local",
        "SUPABASE_URL": "http://supabase.invalid",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role",
        "SUPABASE_JWT_SECRET": "jwt-secret",
        **overrides,
    }
    return Settings(_env_file=None, **values)


def _forbid_gpu_provider(monkeypatch: pytest.MonkeyPatch, module) -> None:
    class ForbiddenProvider:
        def __init__(self, **_kwargs) -> None:
            raise AssertionError("MATTE_PROVIDER=local_cpu must not load the GPU/ML stack")

    monkeypatch.setattr(module, "LocalMLVisualIntelligenceProvider", ForbiddenProvider)


def test_matte_runner_uses_cpu_models_and_records_the_cpu_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workers.matte_job as matte_job

    settings = _runner_settings(MATTE_PROVIDER="local_cpu")
    monkeypatch.setattr(matte_job, "get_settings", lambda: settings)
    _forbid_gpu_provider(monkeypatch, matte_job)
    loaded: dict[str, object] = {}

    def fake_load(*, model_path: str, model_sha256: str):
        loaded.update(path=model_path, sha=model_sha256)
        return {"cv2": object(), "np": object(), "selfie_segmentation": object()}

    monkeypatch.setattr(matte_job, "load_cpu_matte_models", fake_load)
    routes: list[object] = []
    real_register = matte_job.register_matte_route

    def recording_register(gateway, **kwargs):
        route = real_register(gateway, **kwargs)
        routes.append(route)
        return route

    monkeypatch.setattr(matte_job, "register_matte_route", recording_register)
    heavy = ("torch", "transformers", "ultralytics")
    heavy_before = {name for name in heavy if name in sys.modules}

    matte_job._runner()

    assert loaded == {
        "path": "/opt/motionly/models/selfie_segmenter_landscape.tflite",
        "sha": SELFIE_SEGMENTER_LANDSCAPE.sha256,
    }
    (route,) = routes
    assert route.provider == "local_mediapipe_cpu"
    assert route.model == SELFIE_SEGMENTER_LANDSCAPE.name
    assert route.model_revision == SELFIE_SEGMENTER_LANDSCAPE.revision
    heavy_after = {name for name in heavy if name in sys.modules}
    assert heavy_after == heavy_before


def test_matte_runner_default_keeps_the_gpu_route(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.workers.matte_job as matte_job

    settings = _runner_settings()
    monkeypatch.setattr(matte_job, "get_settings", lambda: settings)

    class FakeGpuProvider:
        def __init__(self, **_kwargs) -> None:
            pass

        def _load_models(self):
            return {"cv2": object(), "np": object(), "selfie_segmentation": object()}

    monkeypatch.setattr(matte_job, "LocalMLVisualIntelligenceProvider", FakeGpuProvider)

    def forbidden_cpu_load(**_kwargs):
        raise AssertionError("the default matte path must not use the CPU loader")

    monkeypatch.setattr(matte_job, "load_cpu_matte_models", forbidden_cpu_load)
    routes: list[object] = []
    real_register = matte_job.register_matte_route
    monkeypatch.setattr(
        matte_job,
        "register_matte_route",
        lambda gateway, **kwargs: routes.append(real_register(gateway, **kwargs)) or routes[-1],
    )

    matte_job._runner()

    (route,) = routes
    assert route.provider == "local_mediapipe"
    assert route.model == "mediapipe-selfie-segmentation"
    assert route.model_revision == "mediapipe-selfie-segmentation-v1"


def test_matte_provider_defaults_and_rejects_unknown_values() -> None:
    from pydantic import ValidationError

    assert Settings(_env_file=None).matte_provider == "local_ml"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, MATTE_PROVIDER="gpu-ish")


def test_production_accepts_cpu_matte_and_requires_a_digest() -> None:
    _validate_production_settings(
        _production_settings(process_role="worker", matte_provider="local_cpu")
    )
    with pytest.raises(RuntimeError, match="MATTE_CPU_MODEL_SHA256"):
        _validate_production_settings(
            _production_settings(
                process_role="worker",
                matte_provider="local_cpu",
                matte_cpu_model_sha256="not-a-digest",
            )
        )


def test_dockerfile_bakes_the_pinned_cpu_vision_models() -> None:
    dockerfile = (Path(__file__).resolve().parents[3] / "apps/worker/Dockerfile.python").read_text()

    for model in (SELFIE_SEGMENTER_LANDSCAPE, BLAZE_FACE_FULL_RANGE):
        assert model.url in dockerfile
        assert model.sha256 in dockerfile
        assert model.default_path.rsplit("/", 1)[1] in dockerfile
    assert f"MATTE_CPU_MODEL_SHA256={SELFIE_SEGMENTER_LANDSCAPE.sha256}" in dockerfile
    assert f"VISUAL_CPU_FACE_MODEL_SHA256={BLAZE_FACE_FULL_RANGE.sha256}" in dockerfile


_BAKED_SEGMENTER = Path(
    os.getenv("MATTE_CPU_MODEL_PATH", SELFIE_SEGMENTER_LANDSCAPE.default_path)
)


@pytest.mark.skipif(
    importlib.util.find_spec("mediapipe") is None or not _BAKED_SEGMENTER.is_file(),
    reason="runs inside the cpu-vision image (MediaPipe + baked segmenter)",
)
def test_real_cpu_segmenter_produces_a_matte(tmp_path: Path) -> None:
    from app.services.segmentation_matte import generate_selfie_segmentation_matte

    source = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (256, 144))
    for _ in range(3):
        frame = np.full((144, 256, 3), 30, dtype=np.uint8)
        cv2.ellipse(frame, (128, 90), (40, 60), 0, 0, 360, (150, 170, 200), -1)
        writer.write(frame)
    writer.release()
    models = load_cpu_matte_models(
        model_path=str(_BAKED_SEGMENTER), model_sha256=SELFIE_SEGMENTER_LANDSCAPE.sha256
    )

    stats = generate_selfie_segmentation_matte(
        cv2=models["cv2"],
        np=models["np"],
        selfie_segmentation=models["selfie_segmentation"],
        source_path=source,
        output_path=tmp_path / "matte.mp4",
    )

    assert stats["frame_count"] == 3
    assert (tmp_path / "matte.mp4").stat().st_size > 0
