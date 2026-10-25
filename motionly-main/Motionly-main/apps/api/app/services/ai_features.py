"""Which provider each AI feature uses, where it runs, and whether it is configured.

One place resolves the per-feature switches (VISUAL_PROVIDER, MATTE_PROVIDER,
AUDIO_*_PROVIDER, ...) so the health report and the GPU-free production check
agree with what the workers build. The report never contains secret values:
``configured`` only says whether the selected remote provider has credentials.
"""

from __future__ import annotations

from typing import Literal

Execution = Literal["gpu_worker", "cpu_worker", "remote", "disabled", "fixture"]

GROUP_DISABLED_VALUES = frozenset({"disabled", "none", "off"})


def audio_feature_provider(explicit: str, *, group_disabled: bool) -> str:
    """Resolve one audio feature switch: an explicit value wins over the group."""
    value = str(explicit or "").strip().lower()
    if value:
        return value
    return "disabled" if group_disabled else "local_ml"


def _value(settings: object, name: str, default: str = "") -> str:
    return str(getattr(settings, name, default) or default).strip().lower()


def _has(settings: object, name: str) -> bool:
    return bool(str(getattr(settings, name, "") or "").strip())


def _entry(
    provider: str, execution: Execution, switch: str, *, configured: bool = True
) -> dict[str, object]:
    return {
        "provider": provider,
        "execution": execution,
        "switch": switch,
        "configured": configured,
    }


def ai_feature_report(settings: object) -> dict[str, dict[str, object]]:
    """Per-feature provider, execution site, controlling switch and credentials."""
    report: dict[str, dict[str, object]] = {}

    visual = _value(settings, "visual_intelligence_provider", "local_ml")
    if visual == "remote":
        report["visual_analysis"] = _entry("remote", "cpu_worker", "VISUAL_PROVIDER")
        # Object detection is enabled by a ``vision.object_detection`` registry
        # route (plan Task 17), not a vendor switch; the remote keyframe path
        # reports it as part of the local manifest's CPU worker until Tier 2
        # surfaces per-route capability status.
        report["visual_objects"] = _entry("remote", "cpu_worker", "VISUAL_PROVIDER")
        # Keyframe captions and embeddings are rebuilt as registry capability
        # routes in Tier 2 (plan Task 12); until then the remote path reports them
        # as part of the local manifest's CPU worker (no vendor switch).
        report["visual_captions"] = _entry("remote", "cpu_worker", "VISUAL_PROVIDER")
        report["visual_embeddings"] = _entry("remote", "cpu_worker", "VISUAL_PROVIDER")
    else:
        execution: Execution = "fixture" if visual == "fixture" else "gpu_worker"
        report["visual_analysis"] = _entry(visual, execution, "VISUAL_PROVIDER")
        # Objects, captions and keyframe embeddings are part of the local manifest.
        for feature in ("visual_objects", "visual_captions", "visual_embeddings"):
            report[feature] = _entry(visual, execution, "VISUAL_PROVIDER")

    # Text/image embeddings are rebuilt as a registry capability route in Tier 2
    # (plan Task 12); the EMBEDDINGS_PROVIDER vendor switch is removed. Until the
    # registry route lands, ranking uses local CLIP in the worker that ranks
    # B-roll (and a lexical fallback when no vectors are available).
    report["embeddings"] = _entry("local_clip", "cpu_worker", "EMBEDDINGS_PROVIDER")

    matte = _value(settings, "matte_provider", "local_ml")
    report["matte"] = _entry(
        matte, "cpu_worker" if matte == "local_cpu" else "gpu_worker", "MATTE_PROVIDER"
    )

    group_disabled = _value(settings, "audio_ml_provider", "local_ml") in GROUP_DISABLED_VALUES
    audio_features = (
        ("audio_diarization", "audio_diarization_provider", "AUDIO_DIARIZATION_PROVIDER",
         {}),
        ("audio_emotion", "audio_emotion_provider", "AUDIO_EMOTION_PROVIDER",
         {}),
        ("audio_sound", "audio_sound_provider", "AUDIO_SOUND_PROVIDER",
         {"yamnet_tflite": ("cpu_worker", None)}),
    )
    for feature, attribute, switch, alternatives in audio_features:
        provider = audio_feature_provider(
            str(getattr(settings, attribute, "") or ""), group_disabled=group_disabled
        )
        if provider == "disabled":
            report[feature] = _entry("disabled", "disabled", switch)
        elif provider in alternatives:
            execution, key = alternatives[provider]
            report[feature] = _entry(
                provider, execution, switch, configured=key is None or _has(settings, key)
            )
        else:
            report[feature] = _entry(provider, "gpu_worker", switch)

    stems = _value(settings, "stem_separation_provider", "demucs")
    if stems == "fixture":
        report["stems"] = _entry("fixture", "fixture", "STEM_SEPARATION_PROVIDER")
    else:
        # Demucs runs on the CPU demucs-worker. A remote stem provider is
        # configured through a registry ``audio.stem_separation`` route (plan
        # Task 17), not a vendor switch.
        report["stems"] = _entry(stems, "cpu_worker", "STEM_SEPARATION_PROVIDER")
    return report


def gpu_worker_switches(settings: object) -> list[str]:
    """``SWITCH=value`` for every feature that needs a GPU worker (sorted)."""
    switches = {
        f"{entry['switch']}={entry['provider']}"
        for entry in ai_feature_report(settings).values()
        if entry["execution"] == "gpu_worker"
    }
    if bool(getattr(settings, "ffmpeg_gpu_enabled", False)):
        switches.add("FFMPEG_GPU_ENABLED=true")
    return sorted(switches)


def unconfigured_features(report: dict[str, dict[str, object]]) -> list[str]:
    return sorted(name for name, entry in report.items() if not entry["configured"])
