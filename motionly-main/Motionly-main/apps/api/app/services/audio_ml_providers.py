import hashlib
import importlib.util
import wave
from pathlib import Path
from typing import Any

from app.api.errors import ApiError


def _modules_available(*names: str) -> bool:
    try:
        return all(importlib.util.find_spec(name) is not None for name in names)
    except (ImportError, ModuleNotFoundError):
        return False


def emotion_insights(emotion: object, *, audio_path: Path) -> dict[str, object]:
    """Insights entries for one emotion provider.

    Local providers keep their exact contract: ``emotion_windows`` is whatever
    ``classify_windows`` returns. Providers with ``classify_windows_detailed``
    (remote) also report who produced the windows, and on failure report
    ``emotion_ml.degraded`` *without* windows, so the transcript-based
    estimate is kept instead of being replaced by an empty list.
    """
    detailed = getattr(emotion, "classify_windows_detailed", None)
    if not callable(detailed):
        return {"emotion_windows": emotion.classify_windows(audio_path=audio_path)}
    result = detailed(audio_path=audio_path)
    meta: dict[str, object] = {
        "provider": result.get("provider"),
        "model": result.get("model"),
    }
    if result.get("degraded"):
        return {"emotion_ml": {**meta, "degraded": True, "status": result.get("status")}}
    return {"emotion_windows": list(result.get("windows") or []), "emotion_ml": meta}


class PyannoteSpeakerDiarizationProvider:
    provider_name = "pyannote"

    def __init__(
        self,
        *,
        model: str,
        auth_token: str | None,
        revision: str | None = None,
    ) -> None:
        self.model = model
        self.auth_token = auth_token
        self.revision = revision
        self._pipeline = None

    def check_availability(self) -> tuple[bool, str]:
        if not self.auth_token:
            return False, "auth_token_missing"
        return (
            (True, "available")
            if _modules_available("pyannote.audio")
            else (False, "dependency_missing")
        )

    def diarize(self, *, audio_path: Path) -> dict[str, Any]:
        available, reason = self.check_availability()
        if not available:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "pyannote_speaker_diarization_unavailable",
                extra={"reason": reason, "model": self.model},
            )
            return {
                "provider": self.provider_name,
                "model": self.model,
                "speaker_count": 0,
                "segments": [],
                "confidence": None,
                "degraded": True,
                "status": f"unavailable:{reason}",
            }
        pipeline = self._load_pipeline()
        diarization = pipeline(str(audio_path))
        segments = []
        speakers = set()
        for index, (turn, _track, speaker) in enumerate(diarization.itertracks(yield_label=True)):
            speakers.add(str(speaker))
            segments.append(
                {
                    "speaker": str(speaker),
                    "start_ms": round(float(turn.start) * 1000),
                    "end_ms": round(float(turn.end) * 1000),
                    "segment_index": index,
                }
            )
        return {
            "provider": self.provider_name,
            "model": self.model,
            "speaker_count": len(speakers),
            "segments": segments,
            "confidence": None,
        }

    def _load_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline
        try:
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise ApiError(
                503,
                "pyannote_dependency_missing",
                "Install pyannote.audio before enabling pyannote diarization.",
            ) from exc
        self._pipeline = Pipeline.from_pretrained(
            self.model,
            use_auth_token=self.auth_token,
            revision=self.revision,
        )
        return self._pipeline


class Wav2VecEmotionProvider:
    provider_name = "wav2vec2_emotion"

    def __init__(self, *, model: str, revision: str | None = None) -> None:
        self.model = model
        self.revision = revision
        self._loaded: dict[str, object] | None = None

    def check_availability(self) -> tuple[bool, str]:
        return (
            (True, "available")
            if _modules_available("torch", "torchaudio", "transformers")
            else (False, "dependency_missing")
        )

    def classify_windows(self, *, audio_path: Path, window_ms: int = 500) -> list[dict[str, Any]]:
        available, reason = self.check_availability()
        if not available:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "wav2vec2_emotion_unavailable",
                extra={"reason": reason, "model": self.model},
            )
            return []
        loaded = self._load()
        torch = loaded["torch"]
        torchaudio = loaded["torchaudio"]
        processor = loaded["processor"]
        model = loaded["model"]
        waveform, sample_rate = torchaudio.load(str(audio_path))
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        target_rate = 16000
        if sample_rate != target_rate:
            waveform = torchaudio.functional.resample(waveform, sample_rate, target_rate)
            sample_rate = target_rate
        window_samples = max(1, int(sample_rate * window_ms / 1000))
        labels = getattr(model.config, "id2label", {})
        windows = []
        for start in range(0, waveform.shape[-1], window_samples):
            chunk = waveform[..., start : start + window_samples]
            if chunk.shape[-1] < sample_rate * 0.15:
                continue
            inputs = processor(
                chunk.squeeze().numpy(),
                sampling_rate=sample_rate,
                return_tensors="pt",
            )
            with torch.no_grad():
                logits = model(**inputs).logits
                probabilities = torch.softmax(logits, dim=-1)[0]
            score, label_index = torch.max(probabilities, dim=0)
            emotion_scores = _emotion_scores(
                probabilities=[float(value) for value in probabilities],
                labels=labels,
            )
            windows.append(
                {
                    "start_ms": round(start / sample_rate * 1000),
                    "end_ms": round(
                        min(start + window_samples, waveform.shape[-1]) / sample_rate * 1000
                    ),
                    "emotion": str(labels.get(int(label_index), int(label_index))),
                    "joy": emotion_scores["joy"],
                    "sadness": emotion_scores["sadness"],
                    "urgency": emotion_scores["urgency"],
                    "intensity": round(max(float(score), 1 - emotion_scores["neutral"]), 3),
                }
            )
        return windows

    def _load(self) -> dict[str, object]:
        if self._loaded is not None:
            return self._loaded
        try:
            import torch
            import torchaudio
            from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        except ImportError as exc:
            raise ApiError(
                503,
                "wav2vec_dependency_missing",
                ("Install torch, torchaudio, and transformers before enabling wav2vec emotion."),
            ) from exc
        self._loaded = {
            "torch": torch,
            "torchaudio": torchaudio,
            "processor": AutoFeatureExtractor.from_pretrained(
                self.model,
                revision=self.revision,
            ),
            "model": AutoModelForAudioClassification.from_pretrained(
                self.model,
                revision=self.revision,
            ),
        }
        return self._loaded


def _emotion_scores(*, probabilities: list[float], labels: dict) -> dict[str, float]:
    buckets = {"joy": 0.0, "sadness": 0.0, "urgency": 0.0, "neutral": 0.0}
    for index, probability in enumerate(probabilities):
        label = str(labels.get(index, index)).lower()
        if any(term in label for term in ("joy", "happy", "positive", "excite", "surprise")):
            buckets["joy"] += probability
        elif any(term in label for term in ("sad", "grief", "negative", "disappoint")):
            buckets["sadness"] += probability
        elif any(term in label for term in ("ang", "fear", "urgent", "stress", "disgust")):
            buckets["urgency"] += probability
        else:
            buckets["neutral"] += probability
    total = sum(buckets.values()) or 1
    return {key: round(value / total, 3) for key, value in buckets.items()}


class YamnetTFLiteSoundClassifier:
    """YAMNet sound-event classification on CPU via MediaPipe's TFLite runtime.

    Runs in the torch/TensorFlow-free cpu-vision worker image, which bakes in the
    checksum-pinned ``yamnet.tflite`` model. The model's metadata carries the
    AudioSet class names, so labels are human readable ("Speech", "Music").
    """

    provider_name = "yamnet_tflite"
    # A non-default provider: it changes the audio route bundle revision.
    switch_revision = True
    _SPEECH_LABELS = frozenset({"speech", "narration, monologue", "conversation"})
    _SILENCE_LABELS = frozenset({"silence"})

    def __init__(
        self,
        *,
        model_path: str,
        model_sha256: str | None = None,
        max_results: int = 5,
        max_audio_seconds: int = 600,
    ) -> None:
        self.model_path = model_path
        self.model_sha256 = (model_sha256 or "").strip().lower() or None
        self.model = f"yamnet-tflite:{(self.model_sha256 or 'unpinned')[:12]}"
        self.model_revision = (
            f"sha256:{self.model_sha256}" if self.model_sha256 else "yamnet-tflite-unpinned"
        )
        self.max_results = max_results
        self.max_audio_seconds = max_audio_seconds
        self._verified = False

    def check_availability(self) -> tuple[bool, str]:
        if not _modules_available("mediapipe", "numpy"):
            return False, "dependency_missing"
        path = Path(self.model_path)
        if not path.is_file():
            return False, "model_missing"
        if self.model_sha256 and not self._verified:
            if _sha256_file(path) != self.model_sha256:
                return False, "model_checksum_mismatch"
            self._verified = True
        return True, "available"

    def classify(self, *, audio_path: Path) -> dict[str, Any]:
        available, reason = self.check_availability()
        if not available:
            import logging

            logging.getLogger(__name__).warning(
                "yamnet_tflite_sound_classification_unavailable",
                extra={"reason": reason, "model": self.model},
            )
            return self._degraded(f"unavailable:{reason}")
        try:
            samples, sample_rate = _read_mono_float_samples(
                audio_path, max_seconds=self.max_audio_seconds
            )
        except (OSError, ValueError, EOFError, wave.Error):
            return self._degraded("unavailable:audio_unreadable")
        if samples.size == 0:
            return self._degraded("unavailable:audio_empty")
        try:
            scores = self._mean_scores(samples, sample_rate)
        except (OSError, RuntimeError) as exc:
            # The native MediaPipe runtime failed to load or run (for example a
            # missing system library); keep the transcript-based estimate.
            import logging

            logging.getLogger(__name__).warning(
                "yamnet_tflite_runtime_failed",
                extra={"error_type": type(exc).__name__, "model": self.model},
            )
            return self._degraded("unavailable:runtime_error")
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        labels = [
            {"label": label, "confidence": round(score, 3)}
            for label, score in ranked[: self.max_results]
        ]
        return {
            "provider": self.provider_name,
            "model": self.model,
            "labels": labels,
            "background_sound": _background_sound(
                ranked, ignore=self._SPEECH_LABELS | self._SILENCE_LABELS
            ),
        }

    def _mean_scores(self, samples, sample_rate: int) -> dict[str, float]:
        from mediapipe.tasks.python import audio
        from mediapipe.tasks.python.components.containers import audio_data
        from mediapipe.tasks.python.core import base_options

        options = audio.AudioClassifierOptions(
            base_options=base_options.BaseOptions(model_asset_path=self.model_path),
            # Score every class per window so the clip-level mean is unbiased.
            max_results=-1,
        )
        totals: dict[str, float] = {}
        windows = 0
        with audio.AudioClassifier.create_from_options(options) as classifier:
            results = classifier.classify(
                audio_data.AudioData.create_from_array(samples, sample_rate)
            )
        for result in results:
            if not result.classifications:
                continue
            windows += 1
            for category in result.classifications[0].categories:
                name = str(category.category_name or category.index)
                totals[name] = totals.get(name, 0.0) + float(category.score)
        return {name: total / windows for name, total in totals.items()} if windows else {}

    def _degraded(self, status: str) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "labels": [],
            "degraded": True,
            "status": status,
            # Keep the transcript-based estimate instead of an empty result.
            "heuristic_fallback": True,
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_mono_float_samples(audio_path: Path, *, max_seconds: int):
    """Mono float32 samples in [-1, 1] from a 16-bit PCM WAV (the speech track)."""
    import numpy as np

    with wave.open(str(audio_path), "rb") as reader:
        if reader.getsampwidth() != 2:
            raise ValueError("YAMNet input must be 16-bit PCM WAV")
        sample_rate = reader.getframerate()
        channels = reader.getnchannels()
        frames = reader.readframes(min(reader.getnframes(), sample_rate * max_seconds))
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate


def _background_sound(ranked: list[tuple[str, float]], *, ignore: frozenset[str]) -> str:
    for label, score in ranked:
        if label.lower() in ignore:
            continue
        return label if score >= 0.1 else "none"
    return "none"


class YamnetSoundClassifier:
    provider_name = "yamnet"

    def __init__(self, *, model_url: str) -> None:
        self.model_url = model_url
        self._model = None

    def check_availability(self) -> tuple[bool, str]:
        return (
            (True, "available")
            if _modules_available("numpy", "tensorflow", "tensorflow_hub")
            else (False, "dependency_missing")
        )

    def classify(self, *, audio_path: Path) -> dict[str, Any]:
        available, reason = self.check_availability()
        if not available:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "yamnet_sound_classification_unavailable",
                extra={"reason": reason, "model": self.model_url},
            )
            return {
                "provider": self.provider_name,
                "model": self.model_url,
                "labels": [],
                "degraded": True,
                "status": f"unavailable:{reason}",
            }
        try:
            import numpy as np
            import tensorflow as tf
            import tensorflow_hub as hub
        except ImportError as exc:
            raise ApiError(
                503,
                "yamnet_dependency_missing",
                (
                    "Install tensorflow and tensorflow-hub before enabling "
                    "YAMNet sound classification."
                ),
            ) from exc
        model = self._model or hub.load(self.model_url)
        self._model = model
        audio_binary = tf.io.read_file(str(audio_path))
        waveform, sample_rate = tf.audio.decode_wav(audio_binary, desired_channels=1)
        waveform = tf.squeeze(waveform, axis=-1)
        if int(sample_rate.numpy()) != 16000:
            raise ApiError(
                422,
                "yamnet_sample_rate_invalid",
                "YAMNet expects 16 kHz mono WAV audio.",
            )
        scores, _embeddings, _spectrogram = model(waveform)
        mean_scores = np.mean(scores.numpy(), axis=0)
        top_indexes = mean_scores.argsort()[-5:][::-1]
        return {
            "provider": self.provider_name,
            "model": self.model_url,
            "labels": [
                {
                    "label": f"yamnet_class_{int(index)}",
                    "confidence": round(float(mean_scores[index]), 3),
                }
                for index in top_indexes
            ],
        }
