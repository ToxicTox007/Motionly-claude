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
        remote_parts = (
            ("visual_objects", "visual_objects_provider", "VISUAL_OBJECTS_PROVIDER",
             {"google_vision": "google_vision_api_key"}),
            ("visual_captions", "visual_captions_provider", "VISUAL_CAPTIONS_PROVIDER",
             {"openrouter": "openrouter_api_key"}),
            ("visual_embeddings", "visual_embeddings_provider", "VISUAL_EMBEDDINGS_PROVIDER",
             {"jina": "jina_api_key"}),
        )
        for feature, attribute, switch, keys in remote_parts:
            provider = _value(settings, attribute, "disabled")
            if provider == "disabled":
                report[feature] = _entry("disabled", "disabled", switch)
            else:
                key = keys.get(provider)
                report[feature] = _entry(
                    provider, "remote", switch, configured=bool(key) and _has(settings, key)
                )
    else:
        execution: Execution = "fixture" if visual == "fixture" else "gpu_worker"
        report["visual_analysis"] = _entry(visual, execution, "VISUAL_PROVIDER")
        # Objects, captions and keyframe embeddings are part of the local manifest.
        for feature in ("visual_objects", "visual_captions", "visual_embeddings"):
            report[feature] = _entry(visual, execution, "VISUAL_PROVIDER")

    embeddings = _value(settings, "embeddings_provider", "local_clip")
    report["embeddings"] = (
        _entry("jina", "remote", "EMBEDDINGS_PROVIDER", configured=_has(settings, "jina_api_key"))
        if embeddings == "jina"
        # CLIP runs in the worker process that ranks B-roll.
        else _entry(embeddings, "cpu_worker", "EMBEDDINGS_PROVIDER")
    )

    matte = _value(settings, "matte_provider", "local_ml")
    report["matte"] = _entry(
        matte, "cpu_worker" if matte == "local_cpu" else "gpu_worker", "MATTE_PROVIDER"
    )

    group_disabled = _value(settings, "audio_ml_provider", "local_ml") in GROUP_DISABLED_VALUES
    audio_features = (
        ("audio_diarization", "audio_diarization_provider", "AUDIO_DIARIZATION_PROVIDER",
         {"pyannoteai": ("remote", "pyannoteai_api_key")}),
        ("audio_emotion", "audio_emotion_provider", "AUDIO_EMOTION_PROVIDER",
         {"openrouter_audio": ("remote", "openrouter_api_key")}),
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
    if stems == "lalal":
        report["stems"] = _entry(
            "lalal", "remote", "STEM_SEPARATION_PROVIDER",
            configured=_has(settings, "lalal_license_key"),
        )
    elif stems == "fixture":
        report["stems"] = _entry("fixture", "fixture", "STEM_SEPARATION_PROVIDER")
    else:
        # Demucs runs on the CPU demucs-worker.
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
