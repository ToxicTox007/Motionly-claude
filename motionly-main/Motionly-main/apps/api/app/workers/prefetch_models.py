import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from time import perf_counter

from app.core.config import get_settings
from app.core.production import validate_production_settings


def _enabled_local_models(settings) -> list[dict[str, object]]:
    """The enabled local models from the registry (plan Task 14).

    Reads the built runtime's route + model records: every model behind an
    enabled ``local`` route, with its registry-sourced id, revision, weights
    path/SHA. GPU-only models are skipped on a GPU-free deployment
    (``GPU_WORKERS_ENABLED=false``). No model id, revision, URL or SHA comes from
    a code default; the registry is the only source.
    """
    try:
        from app.services.inference_runtime import build_inference_runtime

        runtime = build_inference_runtime(settings)
    except Exception as exc:  # a missing/invalid registry must not crash prefetch
        print(f"model_prefetch_warning: registry unavailable: {exc}", file=sys.stderr)
        return []
    gpu_enabled = bool(getattr(settings, "gpu_workers_enabled", True))
    document = getattr(runtime.validation, "document", None)
    models_by_id = (
        {model.id: model for model in document.models} if document is not None else {}
    )
    selected: list[dict[str, object]] = []
    seen: set[str] = set()
    for route in runtime.route_records.values():
        adapter = runtime.adapter_records.get(route.adapter_id)
        if adapter is None or adapter.type != "local":
            continue
        if route.requires_gpu and not gpu_enabled:
            continue
        model = models_by_id.get(route.model_ref) if route.model_ref else None
        if model is None or model.model_id in seen:
            continue
        seen.add(model.model_id)
        params = dict(route.params)
        selected.append(
            {
                "model_id": model.model_id,
                "revision": model.revision,
                "weights_sha256": model.weights_sha256,
                "model_path": params.get("model_path"),
            }
        )
    return selected


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    settings = get_settings()
    validate_production_settings(settings)
    if os.getenv("MODEL_PREFETCH", "false").lower() != "true":
        return
    started_at = perf_counter()
    failures: list[str] = []
    attempted = 0
    tmp_cleanup = cleanup_worker_tmp()

    for entry in _enabled_local_models(settings):
        attempted += 1
        model_id = str(entry["model_id"])
        revision = str(entry["revision"])
        weights_sha256 = entry.get("weights_sha256")
        model_path = entry.get("model_path")
        try:
            if model_path:
                # A file-weights model (MediaPipe matte, YAMNet TFLite): verify the
                # pinned SHA-256 of the baked-in file; never download a code URL.
                path = Path(str(model_path))
                if not path.is_file():
                    raise FileNotFoundError(f"weights file missing: {model_path}")
                if weights_sha256:
                    actual = _sha256_file(path)
                    if actual != str(weights_sha256).strip().lower():
                        raise ValueError(
                            f"weights SHA-256 mismatch for {model_id}: "
                            f"expected {weights_sha256}, got {actual}"
                        )
            elif model_id == "htdemucs" and importlib.util.find_spec("demucs"):
                from demucs.pretrained import get_model

                get_model(model_id)
            elif importlib.util.find_spec("huggingface_hub"):
                from huggingface_hub import snapshot_download

                snapshot_download(
                    repo_id=model_id,
                    revision=revision,
                    token=getattr(settings, "pyannote_auth_token", None),
                )
        except Exception as exc:  # worker logs retain the exact provider failure
            failures.append(f"{model_id}@{revision}: {exc}")

    for failure in failures:
        print(f"model_prefetch_warning: {failure}", file=sys.stderr)
    print(
        json.dumps(
            {
                "event": "model_prefetch_completed",
                "duration_ms": round((perf_counter() - started_at) * 1000),
                "models_attempted": attempted,
                "failure_count": len(failures),
                "disk_free_bytes": _disk_free_bytes(settings.media_work_dir),
                "gpu_memory_allocated_mb": _gpu_memory_allocated_mb(),
                "cache_budget": enforce_worker_cache_budget(),
                "tmp_cleanup": tmp_cleanup,
            }
        )
    )
    if failures and os.getenv("MODEL_PREFETCH_STRICT", "false").lower() == "true":
        raise SystemExit(1)


def _disk_free_bytes(path: str) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def cleanup_worker_tmp(
    *,
    tmp_dir: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, int]:
    root = Path(tmp_dir or tempfile.gettempdir())
    max_age = (
        max_age_seconds
        if max_age_seconds is not None
        else _positive_int(os.getenv("WORKER_TMP_MAX_AGE_SECONDS"), 6 * 60 * 60)
    )
    cutoff = time.time() - max_age
    removed = 0
    bytes_removed = 0
    if not root.exists():
        return {"removed": removed, "bytes_removed": bytes_removed}
    for candidate in root.iterdir():
        if not candidate.name.startswith("motionly-"):
            continue
        try:
            stat = candidate.stat()
        except OSError:
            continue
        if stat.st_mtime > cutoff:
            continue
        size = _path_size(candidate)
        try:
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
        except OSError:
            continue
        removed += 1
        bytes_removed += size
    return {"removed": removed, "bytes_removed": bytes_removed}


def enforce_worker_cache_budget(
    *,
    cache_dirs: list[str] | None = None,
    max_bytes: int | None = None,
) -> dict[str, int]:
    budget = (
        max_bytes
        if max_bytes is not None
        else _positive_int(os.getenv("WORKER_CACHE_MAX_BYTES"), 0)
    )
    roots = [
        Path(path)
        for path in (
            cache_dirs
            if cache_dirs is not None
            else [
                os.getenv("HF_HOME"),
                os.getenv("TORCH_HOME"),
                os.getenv("XDG_CACHE_HOME"),
            ]
        )
        if path
    ]
    files = []
    total_bytes = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime, stat.st_size, path))
            total_bytes += stat.st_size
    removed = 0
    bytes_removed = 0
    if budget > 0 and total_bytes > budget:
        for _mtime, size, path in sorted(files):
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
            bytes_removed += size
            total_bytes -= size
            _remove_empty_parents(path.parent, roots)
            if total_bytes <= budget:
                break
    return {
        "budget_bytes": budget,
        "total_bytes": total_bytes,
        "removed": removed,
        "bytes_removed": bytes_removed,
    }


def _path_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        try:
            total += child.stat().st_size
        except OSError:
            continue
    return total


def _remove_empty_parents(path: Path, roots: list[Path]) -> None:
    root_set = {root.resolve() for root in roots if root.exists()}
    current = path
    while current.exists() and current.resolve() not in root_set:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _gpu_memory_allocated_mb() -> int | None:
    if not importlib.util.find_spec("torch"):
        return None
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return round(torch.cuda.memory_allocated() / (1024 * 1024))
    except Exception:
        return None


if __name__ == "__main__":
    main()
