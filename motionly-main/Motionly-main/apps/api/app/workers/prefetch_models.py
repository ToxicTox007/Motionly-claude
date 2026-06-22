import importlib.util
import os
import sys

from app.core.config import get_settings


def main() -> None:
    if os.getenv("MODEL_PREFETCH", "false").lower() != "true":
        return
    settings = get_settings()
    failures: list[str] = []

    if importlib.util.find_spec("huggingface_hub"):
        from huggingface_hub import snapshot_download

        models = [
            settings.visual_clip_model,
            settings.visual_blip_model,
            settings.wav2vec_emotion_model,
        ]
        if settings.pyannote_auth_token:
            models.append(settings.pyannote_model)
        for model in models:
            try:
                snapshot_download(repo_id=model, token=settings.pyannote_auth_token)
            except Exception as exc:  # worker logs retain the exact provider failure
                failures.append(f"{model}: {exc}")

    if importlib.util.find_spec("ultralytics"):
        try:
            from ultralytics import YOLO

            YOLO(settings.visual_yolo_model)
        except Exception as exc:
            failures.append(f"{settings.visual_yolo_model}: {exc}")

    if importlib.util.find_spec("tensorflow_hub"):
        try:
            import tensorflow_hub as hub

            hub.load(settings.yamnet_model_url)
        except Exception as exc:
            failures.append(f"YAMNet: {exc}")

    if importlib.util.find_spec("demucs"):
        try:
            from demucs.pretrained import get_model

            get_model(settings.demucs_model)
        except Exception as exc:
            failures.append(f"Demucs {settings.demucs_model}: {exc}")

    for failure in failures:
        print(f"model_prefetch_warning: {failure}", file=sys.stderr)
    if failures and os.getenv("MODEL_PREFETCH_STRICT", "false").lower() == "true":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
