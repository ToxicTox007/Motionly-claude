from pathlib import Path
from typing import Any

from app.api.errors import ApiError


class PyannoteSpeakerDiarizationProvider:
    provider_name = "pyannote"

    def __init__(self, *, model: str, auth_token: str | None) -> None:
        self.model = model
        self.auth_token = auth_token
        self._pipeline = None

    def check_availability(self) -> tuple[bool, str]:
        if not self.auth_token:
            return False, "auth_token_missing"
        try:
            from pyannote.audio import Pipeline
            return True, "available"
        except ImportError:
            return False, "dependency_missing"

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
        self._pipeline = Pipeline.from_pretrained(self.model, use_auth_token=self.auth_token)
        return self._pipeline


class Wav2VecEmotionProvider:
    provider_name = "wav2vec2_emotion"

    def __init__(self, *, model: str) -> None:
        self.model = model
        self._loaded: dict[str, object] | None = None

    def check_availability(self) -> tuple[bool, str]:
        try:
            import torch
            import torchaudio
            from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
            return True, "available"
        except ImportError:
            return False, "dependency_missing"

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
            "processor": AutoFeatureExtractor.from_pretrained(self.model),
            "model": AutoModelForAudioClassification.from_pretrained(self.model),
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


class YamnetSoundClassifier:
    provider_name = "yamnet"

    def __init__(self, *, model_url: str) -> None:
        self.model_url = model_url
        self._model = None

    def check_availability(self) -> tuple[bool, str]:
        try:
            import numpy as np
            import tensorflow as tf
            import tensorflow_hub as hub
            return True, "available"
        except ImportError:
            return False, "dependency_missing"

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
