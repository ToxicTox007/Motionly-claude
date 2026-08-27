import re
from collections import Counter

from app.repositories.captions import TranscriptRecord
from app.schemas.plans import EditPlanStyle, StyleClassification
from app.schemas.visual import CameraMotion, SceneType, VideoManifest

CANONICAL_STYLES = (
    EditPlanStyle.MOTIVATIONAL,
    EditPlanStyle.EMOTIONAL,
    EditPlanStyle.CINEMATIC,
    EditPlanStyle.EDUCATIONAL,
    EditPlanStyle.LUXURY,
    EditPlanStyle.STORYTELLING,
    EditPlanStyle.DRAMATIC,
    EditPlanStyle.FAST_PACED,
    EditPlanStyle.CLEAN_MINIMAL,
    EditPlanStyle.CREATOR_INFLUENCER,
)

LEGACY_STYLE_CANDIDATES: dict[EditPlanStyle, tuple[EditPlanStyle, ...]] = {
    EditPlanStyle.FOUNDER: (EditPlanStyle.CLEAN_MINIMAL, EditPlanStyle.CREATOR_INFLUENCER),
    EditPlanStyle.TRAVEL: (EditPlanStyle.FAST_PACED, EditPlanStyle.CINEMATIC),
    EditPlanStyle.FASHION: (EditPlanStyle.DRAMATIC, EditPlanStyle.LUXURY),
    EditPlanStyle.PODCAST: (EditPlanStyle.STORYTELLING, EditPlanStyle.CREATOR_INFLUENCER),
}

TERM_WEIGHTS: dict[EditPlanStyle, dict[str, float]] = {
    EditPlanStyle.MOTIVATIONAL: {
        "build": 0.28,
        "believe": 0.35,
        "dream": 0.32,
        "growth": 0.28,
        "prove": 0.28,
        "success": 0.3,
        "win": 0.3,
        "future": 0.24,
    },
    EditPlanStyle.EMOTIONAL: {
        "feel": 0.22,
        "felt": 0.22,
        "heart": 0.3,
        "memory": 0.26,
        "personal": 0.24,
        "quiet": 0.22,
        "loss": 0.32,
        "love": 0.3,
    },
    EditPlanStyle.CINEMATIC: {
        "atmosphere": 0.35,
        "cinematic": 0.45,
        "journey": 0.28,
        "landscape": 0.32,
        "scene": 0.22,
        "story": 0.24,
        "wide": 0.3,
        "travel": 0.22,
    },
    EditPlanStyle.EDUCATIONAL: {
        "explain": 0.3,
        "framework": 0.34,
        "learn": 0.3,
        "lesson": 0.3,
        "step": 0.35,
        "steps": 0.35,
        "teach": 0.34,
        "tutorial": 0.38,
    },
    EditPlanStyle.LUXURY: {
        "atelier": 0.34,
        "brand": 0.26,
        "crafted": 0.34,
        "design": 0.24,
        "elegant": 0.34,
        "exclusive": 0.36,
        "luxury": 0.45,
        "premium": 0.38,
    },
    EditPlanStyle.STORYTELLING: {
        "because": 0.2,
        "journey": 0.26,
        "moment": 0.22,
        "remember": 0.28,
        "story": 0.42,
        "then": 0.16,
        "turning": 0.28,
        "when": 0.18,
    },
    EditPlanStyle.DRAMATIC: {
        "break": 0.3,
        "conflict": 0.34,
        "danger": 0.32,
        "dramatic": 0.45,
        "fail": 0.28,
        "risk": 0.28,
        "stop": 0.3,
        "urgent": 0.36,
    },
    EditPlanStyle.FAST_PACED: {
        "action": 0.3,
        "beat": 0.26,
        "fast": 0.38,
        "momentum": 0.3,
        "quick": 0.3,
        "rapid": 0.34,
        "speed": 0.32,
        "viral": 0.2,
    },
    EditPlanStyle.CLEAN_MINIMAL: {
        "calm": 0.28,
        "clean": 0.38,
        "clear": 0.26,
        "focused": 0.28,
        "minimal": 0.42,
        "product": 0.24,
        "simple": 0.28,
        "subtle": 0.26,
    },
    EditPlanStyle.CREATOR_INFLUENCER: {
        "audience": 0.24,
        "casual": 0.28,
        "content": 0.26,
        "creator": 0.38,
        "follow": 0.24,
        "hook": 0.32,
        "personal": 0.2,
        "viral": 0.28,
    },
}

SCENE_TYPE_WEIGHTS: dict[SceneType, dict[EditPlanStyle, float]] = {
    SceneType.TALKING_HEAD: {
        EditPlanStyle.CREATOR_INFLUENCER: 0.5,
        EditPlanStyle.CLEAN_MINIMAL: 0.28,
    },
    SceneType.SCREEN_RECORDING: {EditPlanStyle.EDUCATIONAL: 0.62},
    SceneType.PRODUCT: {EditPlanStyle.LUXURY: 0.42, EditPlanStyle.CLEAN_MINIMAL: 0.32},
    SceneType.LANDSCAPE: {EditPlanStyle.CINEMATIC: 0.55, EditPlanStyle.EMOTIONAL: 0.18},
    SceneType.CROWD: {EditPlanStyle.FAST_PACED: 0.32, EditPlanStyle.STORYTELLING: 0.26},
    SceneType.TEXT_HEAVY: {EditPlanStyle.EDUCATIONAL: 0.42},
}


def classify_edit_style(
    *,
    transcript: TranscriptRecord,
    visual_manifest: VideoManifest | None,
    requested_style: EditPlanStyle | None,
    prompt: str = "",
    feedback: str | None = None,
) -> StyleClassification:
    scores = {style: 0.05 for style in CANONICAL_STYLES}
    signals: list[str] = []

    tokens = _tokens(" ".join(word.text for word in transcript.words))
    direction_tokens = _tokens(" ".join(part for part in (prompt, feedback or "") if part))
    _score_tokens(scores=scores, tokens=tokens, multiplier=1.0)
    _score_tokens(scores=scores, tokens=direction_tokens, multiplier=1.45)
    if direction_tokens:
        signals.append("direction_text")

    audio = _audio_intelligence(transcript)
    _score_audio(scores=scores, signals=signals, audio=audio)
    _score_visual(scores=scores, signals=signals, visual_manifest=visual_manifest)

    predicted_style = max(CANONICAL_STYLES, key=lambda style: scores[style])
    effective_style = _effective_style(
        requested_style=requested_style,
        predicted_style=predicted_style,
        scores=scores,
        signals=signals,
    )
    normalized = _normalized_scores(scores)
    return StyleClassification(
        predicted_style=predicted_style,
        effective_style=effective_style,
        requested_style=requested_style,
        confidence=_confidence(scores=scores, style=effective_style),
        scores={style.value: normalized[style] for style in CANONICAL_STYLES},
        signals=signals[:12],
    )


def _score_tokens(
    *,
    scores: dict[EditPlanStyle, float],
    tokens: list[str],
    multiplier: float,
) -> None:
    counts = Counter(tokens)
    for style, weights in TERM_WEIGHTS.items():
        for term, weight in weights.items():
            hits = counts.get(term, 0)
            if hits:
                scores[style] += min(0.9, hits * weight * multiplier)


def _score_audio(
    *,
    scores: dict[EditPlanStyle, float],
    signals: list[str],
    audio: dict[str, object],
) -> None:
    style_signals = audio.get("style_signals")
    if isinstance(style_signals, dict):
        predicted = _style_from_string(style_signals.get("predicted_style"))
        if predicted:
            scores[predicted] += 0.42
            signals.append(f"audio_style:{predicted.value}")
        tone = str(style_signals.get("tone") or "")
        pace = str(style_signals.get("pace") or "")
        if tone == "urgent":
            scores[EditPlanStyle.DRAMATIC] += 0.45
            signals.append("urgent_tone")
        if tone == "confident":
            scores[EditPlanStyle.MOTIVATIONAL] += 0.28
            scores[EditPlanStyle.CREATOR_INFLUENCER] += 0.16
            signals.append("confident_tone")
        if pace == "fast":
            scores[EditPlanStyle.FAST_PACED] += 0.4
            signals.append("fast_speech")
        if pace == "slow":
            scores[EditPlanStyle.EMOTIONAL] += 0.28
            scores[EditPlanStyle.CINEMATIC] += 0.2
            signals.append("slow_speech")

    speech_metrics = audio.get("speech_metrics")
    if isinstance(speech_metrics, dict):
        wpm = speech_metrics.get("words_per_minute")
        if isinstance(wpm, int | float):
            if wpm >= 185:
                scores[EditPlanStyle.FAST_PACED] += 0.28
            elif 0 < wpm <= 105:
                scores[EditPlanStyle.EMOTIONAL] += 0.18
                scores[EditPlanStyle.CLEAN_MINIMAL] += 0.14

    emotion = audio.get("emotion_pacing")
    if isinstance(emotion, dict):
        aggregate = str(emotion.get("aggregate_emotion") or "").lower()
        if aggregate in {"positive", "happy", "joy"}:
            scores[EditPlanStyle.MOTIVATIONAL] += 0.26
        if aggregate in {"sad", "fear", "tender", "emotional"}:
            scores[EditPlanStyle.EMOTIONAL] += 0.35
        if aggregate in {"angry", "urgent", "intense"}:
            scores[EditPlanStyle.DRAMATIC] += 0.3

    diarization = audio.get("speaker_diarization")
    if isinstance(diarization, dict) and int(diarization.get("speaker_count") or 0) >= 2:
        scores[EditPlanStyle.STORYTELLING] += 0.28
        scores[EditPlanStyle.CREATOR_INFLUENCER] += 0.12
        signals.append("multi_speaker_audio")


def _score_visual(
    *,
    scores: dict[EditPlanStyle, float],
    signals: list[str],
    visual_manifest: VideoManifest | None,
) -> None:
    if visual_manifest is None:
        return
    if visual_manifest.scenes:
        signals.append("visual_manifest")
    for scene in visual_manifest.scenes[:12]:
        importance = max(0.4, scene.visual_importance_score)
        for style, weight in SCENE_TYPE_WEIGHTS.get(scene.scene_type, {}).items():
            scores[style] += weight * importance
        _score_tokens(scores=scores, tokens=_tokens(scene.caption), multiplier=1.15 * importance)
        for obj in scene.objects[:8]:
            _score_tokens(scores=scores, tokens=_tokens(obj.label), multiplier=0.9 * importance)
        if scene.camera_motion in {CameraMotion.PAN, CameraMotion.TILT, CameraMotion.PUSH_IN}:
            scores[EditPlanStyle.CINEMATIC] += 0.18 * importance
        if scene.camera_motion == CameraMotion.HANDHELD:
            scores[EditPlanStyle.FAST_PACED] += 0.18 * importance
            scores[EditPlanStyle.DRAMATIC] += 0.1 * importance
        if scene.camera_motion == CameraMotion.STATIC:
            scores[EditPlanStyle.CLEAN_MINIMAL] += 0.12 * importance
        if scene.faces and scene.scene_type == SceneType.TALKING_HEAD:
            scores[EditPlanStyle.CREATOR_INFLUENCER] += 0.22 * importance


def _effective_style(
    *,
    requested_style: EditPlanStyle | None,
    predicted_style: EditPlanStyle,
    scores: dict[EditPlanStyle, float],
    signals: list[str],
) -> EditPlanStyle:
    if requested_style is None:
        signals.append("auto_style")
        return predicted_style
    if requested_style in CANONICAL_STYLES:
        signals.append("explicit_style")
        return requested_style
    candidates = LEGACY_STYLE_CANDIDATES.get(requested_style)
    if candidates:
        signals.append(f"legacy_style:{requested_style.value}")
        return max(candidates, key=lambda style: scores[style])
    return predicted_style


def _audio_intelligence(transcript: TranscriptRecord) -> dict[str, object]:
    audio = transcript.raw_payload.get("audio_intelligence")
    return audio if isinstance(audio, dict) else {}


def _style_from_string(value: object) -> EditPlanStyle | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_")
    aliases = {
        "creator": EditPlanStyle.CREATOR_INFLUENCER,
        "influencer": EditPlanStyle.CREATOR_INFLUENCER,
        "clean": EditPlanStyle.CLEAN_MINIMAL,
        "minimal": EditPlanStyle.CLEAN_MINIMAL,
        "fast": EditPlanStyle.FAST_PACED,
        "fast_paced": EditPlanStyle.FAST_PACED,
        "founder": EditPlanStyle.CLEAN_MINIMAL,
        "travel": EditPlanStyle.CINEMATIC,
        "fashion": EditPlanStyle.DRAMATIC,
        "podcast": EditPlanStyle.STORYTELLING,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        style = EditPlanStyle(normalized)
    except ValueError:
        return None
    return style if style in CANONICAL_STYLES else None


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9']+", text.lower()) if len(token) >= 3]


def _normalized_scores(scores: dict[EditPlanStyle, float]) -> dict[EditPlanStyle, float]:
    max_score = max(scores.values(), default=1)
    if max_score <= 0:
        return {style: 0 for style in CANONICAL_STYLES}
    return {style: round(scores[style] / max_score, 3) for style in CANONICAL_STYLES}


def _confidence(*, scores: dict[EditPlanStyle, float], style: EditPlanStyle) -> float:
    ordered = sorted(scores.values(), reverse=True)
    top = scores.get(style, 0)
    runner_up = ordered[1] if len(ordered) > 1 else 0
    margin = max(0, top - runner_up)
    return round(min(0.97, max(0.35, 0.52 + margin * 0.22)), 3)
