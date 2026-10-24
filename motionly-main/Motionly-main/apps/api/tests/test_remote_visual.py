"""GPU-free visual analysis (VISUAL_PROVIDER=remote): CPU signals + keyframe enrichment."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.core.config import Settings
from app.main import _validate_production_settings
from app.repositories.captions import TranscriptRecord
from app.schemas.captions import LanguageHint, TranscriptStatus, TranscriptWord
from app.schemas.uploads import VideoMetadata
from app.schemas.visual import (
    BoundingBox,
    FaceDetection,
    ObjectDetection,
    SceneType,
    VideoManifest,
)
from app.services.clip_finder import ClipGoal, find_best_clips
from app.services.cpu_vision import (
    BLAZE_FACE_FULL_RANGE,
    CpuVisionModelError,
    TasksFaceDetector,
)
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo
from app.services.remote_visual import (
    EnrichmentUnavailable,
    RemoteVisualIntelligenceProvider,
    _dominant_colors,
    remote_visual_identity,
)
from app.services.style_classifier import classify_edit_style
from app.services.visual_jobs import visual_provider_identity
from app.services.visual_tracking import subject_track_from_manifest
from tests.test_service_hardening import _production_settings

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

FACE = FaceDetection(confidence=0.9, box=BoundingBox(x=0.4, y=0.2, width=0.2, height=0.2))


def _video(tmp_path: Path, *, colors=((200, 40, 40), (40, 40, 200)), seconds_each=1.5):
    """A short video with one visually distinct scene per colour (BGR)."""
    path = tmp_path / "source.mp4"
    fps = 10
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120))
    for color in colors:
        for frame_index in range(int(fps * seconds_each)):
            frame = np.full((120, 160, 3), color, dtype=np.uint8)
            x = 10 + frame_index * 4
            cv2.rectangle(frame, (x, 40), (x + 20, 80), (255, 255, 255), -1)
            writer.write(frame)
    writer.release()
    return path


def _metadata(duration_ms: int = 3000) -> VideoMetadata:
    return VideoMetadata(duration_ms=duration_ms, width=160, height=120, has_audio=False)


class FakeFaces:
    def __init__(self, faces=(FACE,)) -> None:
        self.faces = list(faces)
        self.calls = 0

    def detect(self, rgb):
        self.calls += 1
        return list(self.faces)


class RecordingEnricher:
    def __init__(self, capability: str, result_for, model_version: str = "test-v1") -> None:
        self.capability = capability
        self.model_version = model_version
        self.result_for = result_for
        self.keyframes: list = []

    def enrich(self, keyframes):
        self.keyframes = list(keyframes)
        if isinstance(self.result_for, Exception):
            raise self.result_for
        return [self.result_for(keyframe) for keyframe in keyframes]


def _provider(*, enrichers=(), faces=None, **overrides) -> RemoteVisualIntelligenceProvider:
    detector = faces if faces is not None else FakeFaces()
    options = {
        "face_model_path": "/models/blaze_face_full_range.tflite",
        "face_model_sha256": BLAZE_FACE_FULL_RANGE.sha256,
        "enrichers": enrichers,
        "face_detector_factory": lambda: detector,
        **overrides,
    }
    return RemoteVisualIntelligenceProvider(**options)


def _analyze(provider, tmp_path: Path, **video_kwargs) -> VideoManifest:
    return provider.analyze(
        source_path=_video(tmp_path, **video_kwargs),
        metadata=_metadata(),
        sample_rate_fps=2.0,
        scene_threshold=0.42,
    )


def test_cpu_signals_build_a_valid_manifest_without_remote_enrichers(tmp_path: Path) -> None:
    manifest = _analyze(_provider(), tmp_path)

    VideoManifest.model_validate(manifest.model_dump())
    assert manifest.provider == "remote_visual"
    assert len(manifest.scenes) >= 2
    assert manifest.frame_count == len(manifest.frame_samples) > 0
    for sample in manifest.frame_samples:
        assert sample.dominant_colors and sample.faces
        assert sample.caption == "" and sample.objects == [] and sample.embedding == []
        assert sample.scene_type == SceneType.TALKING_HEAD
    summary = manifest.summary
    assert summary["execution"] == "cpu_remote"
    assert summary["degraded_capabilities"] == {
        "captions": "unavailable:not_configured",
        "embeddings": "unavailable:not_configured",
        "objects": "unavailable:not_configured",
    }
    assert summary["remote_keyframes"] == {
        "selected": len(manifest.scenes),
        "scenes": len(manifest.scenes),
    }
    versions = manifest.model_versions
    assert versions["object_detection"] == versions["captions"] == versions["clip"] == "unavailable"
    assert versions["face_detection"].endswith(BLAZE_FACE_FULL_RANGE.sha256)
    assert versions["frame_sampling"].startswith("opencv-")


def test_one_keyframe_per_scene_is_sent_and_results_fill_the_scene(tmp_path: Path) -> None:
    laptop = ObjectDetection(
        label="laptop", confidence=0.9, box=BoundingBox(x=0.1, y=0.1, width=0.5, height=0.5)
    )
    objects = RecordingEnricher("objects", lambda keyframe: [laptop])
    captions = RecordingEnricher("captions", lambda keyframe: f"scene {keyframe.scene_index}")
    embeddings = RecordingEnricher("embeddings", lambda keyframe: [0.1, 0.2, 0.3])

    manifest = _analyze(
        _provider(enrichers=(objects, captions, embeddings), faces=FakeFaces(faces=())),
        tmp_path,
    )

    scenes = manifest.scenes
    assert [keyframe.scene_index for keyframe in objects.keyframes] == [
        scene.index for scene in scenes
    ]
    for keyframe in objects.keyframes:
        assert keyframe.jpeg.startswith(b"\xff\xd8")
        assert (keyframe.width, keyframe.height) == (160, 120)
    for sample in manifest.frame_samples:
        assert [item.label for item in sample.objects] == ["laptop"]
        assert sample.caption.startswith("scene ")
        assert sample.embedding == [0.1, 0.2, 0.3]
        # Recomputed from the remote objects: a laptop means a screen recording.
        assert sample.scene_type == SceneType.SCREEN_RECORDING
    assert {scene.caption for scene in scenes} == {f"scene {scene.index}" for scene in scenes}
    assert manifest.summary["degraded_capabilities"] == {}
    assert manifest.model_versions["object_detection"] == "test-v1"
    assert manifest.model_versions["clip"] == "test-v1"


def test_each_remote_capability_degrades_on_its_own(tmp_path: Path) -> None:
    unavailable = ProviderCallFailure(
        provider="openrouter", error_info=ProviderErrorInfo("provider_unavailable", True)
    )
    objects = RecordingEnricher("objects", lambda keyframe: [])
    captions = RecordingEnricher("captions", unavailable)

    class WrongCount(RecordingEnricher):
        def enrich(self, keyframes):
            return [[0.5]]

    embeddings = WrongCount("embeddings", None)

    manifest = _analyze(_provider(enrichers=(objects, captions, embeddings)), tmp_path)

    assert manifest.summary["degraded_capabilities"] == {
        "captions": "unavailable:provider_unavailable",
        "embeddings": "unavailable:provider_malformed_response",
    }
    assert manifest.model_versions["object_detection"] == "test-v1"
    assert manifest.model_versions["captions"] == "unavailable"
    assert manifest.model_versions["clip"] == "unavailable"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (EnrichmentUnavailable("quota_exceeded"), "unavailable:quota_exceeded"),
        (RuntimeError("bug"), "unavailable:enrichment_failed"),
    ],
)
def test_enricher_errors_never_fail_the_manifest(tmp_path: Path, error, status) -> None:
    manifest = _analyze(_provider(enrichers=(RecordingEnricher("objects", error),)), tmp_path)

    assert manifest.summary["degraded_capabilities"]["objects"] == status


def test_malformed_results_degrade(tmp_path: Path) -> None:
    objects = RecordingEnricher("objects", lambda keyframe: ["not-a-detection"])
    captions = RecordingEnricher("captions", lambda keyframe: 42)

    manifest = _analyze(_provider(enrichers=(objects, captions)), tmp_path)

    degraded = manifest.summary["degraded_capabilities"]
    assert degraded["objects"] == degraded["captions"] == "unavailable:provider_malformed_response"


def test_max_keyframes_caps_paid_calls(tmp_path: Path) -> None:
    objects = RecordingEnricher(
        "objects",
        lambda keyframe: [
            ObjectDetection(
                label="bottle", confidence=0.8, box=BoundingBox(x=0, y=0, width=1, height=1)
            )
        ],
    )

    manifest = _analyze(
        _provider(enrichers=(objects,), max_keyframes=1),
        tmp_path,
        colors=((200, 40, 40), (40, 40, 200), (40, 200, 40)),
        seconds_each=1.0,
    )

    assert len(objects.keyframes) == 1
    assert manifest.summary["remote_keyframes"]["selected"] == 1
    assert len(manifest.scenes) >= 2
    labelled = [sample for sample in manifest.frame_samples if sample.objects]
    assert labelled and len(labelled) < len(manifest.frame_samples)


def test_captions_are_whitespace_normalised_and_truncated(tmp_path: Path) -> None:
    captions = RecordingEnricher("captions", lambda keyframe: "  a\n\nlong   " + "x" * 400)

    manifest = _analyze(_provider(enrichers=(captions,)), tmp_path)

    caption = manifest.frame_samples[0].caption
    assert caption.startswith("a long x") and len(caption) == 240


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (CpuVisionModelError("missing"), "unavailable:model_missing_or_mismatch"),
        (ImportError("mediapipe"), "unavailable:dependency_missing"),
        (OSError("libGLESv2.so.2"), "unavailable:runtime_error"),
    ],
)
def test_face_model_problems_degrade_faces_only(tmp_path: Path, error, status) -> None:
    def broken():
        raise error

    manifest = _analyze(_provider(face_detector_factory=broken), tmp_path)

    assert manifest.summary["degraded_capabilities"]["faces"] == status
    assert manifest.model_versions["face_detection"] == "unavailable"
    assert all(sample.faces == [] for sample in manifest.frame_samples)


def test_face_detection_runtime_failure_stops_further_calls(tmp_path: Path) -> None:
    class Flaky:
        calls = 0

        def detect(self, rgb):
            Flaky.calls += 1
            raise RuntimeError("native crash")

    manifest = _analyze(_provider(faces=Flaky()), tmp_path)

    assert Flaky.calls == 1
    assert manifest.summary["degraded_capabilities"]["faces"] == "unavailable:runtime_error"


def test_unreadable_video_is_a_clear_error(tmp_path: Path) -> None:
    garbage = tmp_path / "broken.mp4"
    garbage.write_bytes(b"not a video")

    with pytest.raises(ApiError) as raised:
        _provider().analyze(
            source_path=garbage, metadata=_metadata(), sample_rate_fps=2.0, scene_threshold=0.42
        )

    assert raised.value.status_code == 422


def test_dominant_colours_match_the_gpu_quantisation() -> None:
    red = np.zeros((32, 48, 3), dtype=np.uint8)
    red[...] = (255, 0, 0)

    (color,) = _dominant_colors(cv2=cv2, np=np, rgb=red)

    assert (color.hex, color.rgb, color.percentage) == ("#ff0000", (255, 0, 0), 1.0)


def test_downstream_consumers_accept_a_manifest_without_remote_results(tmp_path: Path) -> None:
    manifest = _analyze(_provider(), tmp_path)
    words = [
        TranscriptWord(index=0, text="Hello", start_ms=0, end_ms=500, confidence=0.9),
        TranscriptWord(index=1, text="world", start_ms=520, end_ms=1200, confidence=0.9),
    ]
    transcript = TranscriptRecord(
        id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        status=TranscriptStatus.READY,
        language="en",
        language_hint=LanguageHint.AUTO,
        provider="fixture",
        model="fixture",
        confidence=0.9,
        duration_ms=3000,
        words=words,
    )

    style = classify_edit_style(
        transcript=transcript, visual_manifest=manifest, requested_style=None
    )
    groups, availability, _count = find_best_clips(
        words=words, source_duration_ms=3000, goal=ClipGoal.VIRAL_HOOK, visual_manifest=manifest
    )

    assert style.predicted_style is not None
    assert availability is not None and isinstance(groups, list)
    assert subject_track_from_manifest(manifest) is not None


def test_tasks_face_detector_normalises_boxes_and_caps_faces() -> None:
    detection = SimpleNamespace(
        bounding_box=SimpleNamespace(origin_x=40, origin_y=30, width=80, height=60),
        categories=[SimpleNamespace(score=0.91234)],
        keypoints=[SimpleNamespace(x=0.5, y=0.25)],
    )
    off_edge = SimpleNamespace(
        bounding_box=SimpleNamespace(origin_x=-10, origin_y=-5, width=400, height=400),
        categories=[SimpleNamespace(score=1.4)],
        keypoints=[],
    )

    class Detector:
        def detect(self, image):
            return SimpleNamespace(detections=[detection, off_edge] * 10)

    mp = SimpleNamespace(
        ImageFormat=SimpleNamespace(SRGB="srgb"),
        Image=lambda *, image_format, data: {"format": image_format, "data": data},
    )
    faces = TasksFaceDetector(detector=Detector(), mp_module=mp, np_module=np).detect(
        np.zeros((120, 160, 3), dtype=np.uint8)
    )

    assert len(faces) == 12
    first, second = faces[0], faces[1]
    assert (first.box.x, first.box.y, first.box.width, first.box.height) == (0.25, 0.25, 0.5, 0.5)
    assert first.confidence == 0.912
    assert first.landmarks == {"point_0": (0.5, 0.25)}
    assert (second.box.x, second.box.y, second.box.width, second.confidence) == (0, 0, 1, 1)


def test_provider_rejects_bad_configuration() -> None:
    with pytest.raises(ValueError, match="max_keyframes"):
        _provider(max_keyframes=0)
    with pytest.raises(ValueError, match="enrichers"):
        _provider(enrichers=(RecordingEnricher("faces", None),))
    with pytest.raises(ValueError, match="enrichers"):
        duplicate = (RecordingEnricher("objects", None), RecordingEnricher("objects", None))
        _provider(enrichers=duplicate)


# -- wiring --------------------------------------------------------------------

# The foundation runs with every remote keyframe enricher switched off.
NO_REMOTE_ENRICHERS = {
    "VISUAL_OBJECTS_PROVIDER": "disabled",
    "VISUAL_CAPTIONS_PROVIDER": "disabled",
    "VISUAL_EMBEDDINGS_PROVIDER": "disabled",
}


def _settings(**overrides) -> Settings:
    values = {
        "APP_ENV": "local",
        "SUPABASE_URL": "http://supabase.invalid",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role",
        "SUPABASE_JWT_SECRET": "jwt-secret",
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_visual_runner_uses_the_remote_provider_without_gpu_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workers.visual_job as visual_job

    settings = _settings(VISUAL_PROVIDER="remote", **NO_REMOTE_ENRICHERS)
    monkeypatch.setattr(visual_job, "get_settings", lambda: settings)

    class Forbidden:
        def __init__(self, **_kwargs) -> None:
            raise AssertionError("VISUAL_PROVIDER=remote must not build the GPU provider")

    monkeypatch.setattr(visual_job, "LocalMLVisualIntelligenceProvider", Forbidden)
    routes: list[object] = []
    real_register = visual_job.register_visual_route

    def recording_register(gateway, **kwargs):
        routes.append((kwargs["provider"], real_register(gateway, **kwargs)))
        return routes[-1][1]

    monkeypatch.setattr(visual_job, "register_visual_route", recording_register)
    heavy = ("torch", "transformers", "ultralytics")
    before = {name for name in heavy if name in sys.modules}

    visual_job._runner()

    ((provider, route),) = routes
    assert isinstance(provider, RemoteVisualIntelligenceProvider)
    assert route.provider == "remote_visual"
    assert route.model == remote_visual_identity(settings)
    assert route.model_revision.startswith("sha256:")
    assert {name for name in heavy if name in sys.modules} == before


def test_remote_identity_feeds_the_visual_cache_key() -> None:
    default = _settings()
    remote = _settings(VISUAL_PROVIDER="remote")
    other_face = _settings(VISUAL_PROVIDER="remote", VISUAL_CPU_FACE_MODEL_SHA256="a" * 64)

    assert visual_provider_identity(default)[0] == "local_ml"
    provider, identity = visual_provider_identity(remote)
    assert provider == "remote" and identity == remote_visual_identity(remote)
    assert identity.startswith("remote|faces:3698b18f063")
    assert visual_provider_identity(other_face)[1] != identity


def test_production_accepts_remote_visual_and_requires_a_face_digest() -> None:
    foundation = {
        "visual_intelligence_provider": "remote",
        "visual_objects_provider": "disabled",
        "visual_captions_provider": "disabled",
        "visual_embeddings_provider": "disabled",
    }
    _validate_production_settings(_production_settings(process_role="worker", **foundation))
    with pytest.raises(RuntimeError, match="VISUAL_CPU_FACE_MODEL_SHA256"):
        _validate_production_settings(
            _production_settings(
                process_role="worker",
                visual_cpu_face_model_sha256="latest",
                **foundation,
            )
        )
    with pytest.raises(RuntimeError, match="VISUAL_PROVIDER"):
        _validate_production_settings(
            _production_settings(visual_intelligence_provider="gpu-cloud")
        )


_BAKED_FACE_MODEL = Path(
    os.getenv("VISUAL_CPU_FACE_MODEL_PATH", BLAZE_FACE_FULL_RANGE.default_path)
)


@pytest.mark.skipif(
    importlib.util.find_spec("mediapipe") is None or not _BAKED_FACE_MODEL.is_file(),
    reason="runs inside the cpu-vision image (MediaPipe + baked BlazeFace model)",
)
def test_real_cpu_pipeline_runs_end_to_end(tmp_path: Path) -> None:
    provider = RemoteVisualIntelligenceProvider(
        face_model_path=str(_BAKED_FACE_MODEL),
        face_model_sha256=BLAZE_FACE_FULL_RANGE.sha256,
    )

    manifest = _analyze(provider, tmp_path)

    assert "faces" not in manifest.summary["degraded_capabilities"]
    assert manifest.model_versions["face_detection"].endswith(BLAZE_FACE_FULL_RANGE.sha256)
    assert len(manifest.scenes) >= 2
